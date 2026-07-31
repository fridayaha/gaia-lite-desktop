# ADR-015: AG-UI Agent 驱动图探索画布（Controlled Gen UI + Shared State）

| 字段 | 值 |
|------|-----|
| 状态 | Accepted |
| 日期 | 2026-07-04 |
| 决策者 | 开发者 + 评审（2026-07-04 评审通过 5 决策点） |
| 影响 | `services/ai_agent.py`、`routes/ai.py`、`tools/state.py`、`routes/query/__init__.py`、前端 `pages/GraphExplorePage.tsx`、`hooks/useGraphExploreAgent.ts` |
| 关联文档 | [graph-reasoning-frontend-design-v3.md](./graph-reasoning-frontend-design-v3.md)（其 §explore-plan 机制被本 ADR 取代）、[adr-009-ontology-tool-layer.md](./adr-009-ontology-tool-layer.md)、[ai-integration-guide.md](../engineer/ai-integration-guide.md) |
| 取代 | `explore-plan` 一次性编排机制（`explore_plan_parser.py` / `ExplorePlan` schema / `usePlanExecutor` hook）、`should_route_to_object_set` 关键词路由 |

## 背景

图探索页面（`GraphExplorePage`）的「分析类」自然语言查询（如「分析供应链中断风险」「查看对象的地理分布」）存在两类根因问题：

### 问题 1：`explore-plan` 一次性编排，不基于状态

`POST /objects/{ont}/explore-plan` 让 LLM 在**第 0 步、空画布、不看任何数据**的情况下，一口气编排出 4-5 步完整计划（load→search_around→color_by→...）。前端 `usePlanExecutor` 逐步执行，**不把中间结果反馈给决策器**。

这违反 Agent 基本范式（ReAct：每步基于当前状态 + 动作空间决策），导致：
- Marketing 本体问「分析供应链中断风险」→ LLM 硬凑出 5 步计划（拿 Dealership 当"供应链"代理）→ 第 1 步 load 返回 0 对象 → 后续 4 步空转 → 最后 LLM 编造一句"重点关注退市车型"的结论。0 对象本应终止，却一路空转到底还给出无数据支撑的断言。

### 问题 2：`should_route_to_object_set` 关键词路由，永远枚举不全

`query-nl` 用硬编码关键词列表（`_GRAPH/_SPATIAL/_TEMPORAL_KEYWORDS`）判定是否走推理线。漏词即误拒：「查看对象的地理分布」因不含「300km/范围内」等量词被判为「非推理查询」拒绝。这是 CLAUDE.md 红线 8 禁止的「手写 if-elif 链 / 手写正则」反模式的变体——把意图理解降级成字符串匹配。

### 现状：两套并行未整合的方案

Gaia 已有 ADR-009 的 AG-UI Agent（pydantic-ai ReAct 循环 + 6 toolset），但**只用在 `OntologyWorkspace` 建模对话**，图探索页面未接入，反而自研了 `explore-plan` 这个劣化版「伪 Agent」。两者从未整合。

## 决策

把图探索的「分析类」问题改走 AG-UI Agent（ReAct 循环 + 工具调用），废弃 `explore-plan` 一次性编排。Agent 的工具调用通过 AG-UI shared state 驱动画布。交互模式采用业界成熟的 **Controlled Gen UI + Shared State 混合模式**。

### D1: 交互模式 — Controlled Gen UI + Shared State（混合）

经检索业界成熟方案（Palantir AIP Agent-Driven Dashboard、CopilotKit agent-driven canvas、AG-UI 协议、Agent Cookbook「Generative UI Is the New Frontend」），Agent 驱动 UI 分三种模式：

| 模式 | 谁拥有 UI | 适用 |
|------|----------|------|
| Controlled | 前端预建组件 | ≤10 高价值流程，UI 需精确 |
| Declarative (A2UI) | Agent 发 schema | 大量卡片/组件 |
| Open-ended | Agent 写裸 HTML | 一次性丢弃式 |

图探索画布是 Gaia 精心设计的固定 UI（Cytoscape 图谱/地图/分屏），不是 Agent 即兴生成的。Agent Cookbook 决策树：「Designer has pixel-perfect mockups? → Controlled」。故画布本身走 Controlled（Agent 不"画"画布，只操控既有画布）。

但画布**状态**（当前对象集、视图、着色）由 Agent 通过 AG-UI shared state 驱动——这是 CopilotKit「AI Dashboard Canvas Agent」和 Palantir「application state variables」的标准模式：

> "Shared state is most powerful when the agent's state shows up in your application UI, such as a dashboard, **document canvas, map, or table**." — CopilotKit docs

### D2: 工具划分 — 后端数据工具 + 前端 UI 工具

AG-UI 官方 Tools 文档明确区分两类工具：

> "Backend-defined tools stay in the backend... Client-defined tools are passed in `RunAgentInput.tools`... for **application-specific frontend behavior, such as UI actions**."

据此划分：

| 工具 | 归属 | 机制 | 例子 |
|------|------|------|------|
| `query_with_dataframe` / `traverse_link` | **后端 toolset**（已有，ADR-009） | 数据查询，返回对象集 + 写 shared state | 加载 Dealership、展开关系 |
| `switch_view` / `color_by` | **前端 tool**（`RunAgentInput.tools`） | 纯 UI 操控，前端执行 + 写 state | 切地图、着色 |

- 数据类：Agent 调后端工具 → 工具返回 `ToolReturn(return_value=数据, metadata=StateSnapshotEvent)` → `return_value` 给 Agent（ReAct observe，含 `objects_count`，0 对象时 Agent 自然终止），`metadata` 给前端（state 事件驱动画布重绘）。**双职分离**是 pydantic-ai 官方推荐模式（见 [pydantic-ai AG-UI docs](https://pydantic.dev/docs/ai/integrations/ui/ag-ui/) §Tools/Events）。
- 纯 UI 类：Agent 调前端 tool → 前端直接执行画布动作 + 写 state。不经后端。

**MVP 妥协（2026-07-04）**：`switch_view`/`color_by` 暂作为**后端 toolset** 实现（`canvas_control.py`，只写 state 不查数据），而非真正的 frontend-defined tool。原因：frontend-defined tool 需前端 `@ag-ui/client` 注册工具 + 处理 `TOOL_CALL_*` 生命周期。但检索确认 pydantic-ai 2.0 的 `AGUIAdapter` 已原生封装 `_AGUIFrontendToolset`（自动把 `RunAgentInput.tools` 包成 toolset 挂给 Agent），真正的 frontend tools 可行——留作 §后续工作。当前 MVP 的后端 canvas_control 工具已能跑通「Agent 驱动画布」核心场景，且通过 `ToolReturn` 双职分离对齐官方模式。

**纯 UI 操控工具不进后端 toolset**——既符合 AG-UI 官方划分，也避免给 MCP/REST 入口暴露无意义的"切视图"工具（MCP 外部 Agent 没有画布可操控）。

### D3: Shared State 结构 — `CanvasSnapshot`

采用 pydantic-ai 2.0 原生 AG-UI shared state 机制（`StateDeps[CanvasSnapshot]` + 工具返回 `StateSnapshotEvent`）。官方模板见 pydantic-ai `examples/ag_ui/api/shared_state.py`（`RecipeSnapshot`）。

```python
class CanvasSnapshot(BaseModel):
    """图探索画布的 AG-UI shared state。前端订阅重绘，Agent 每轮读取决策。"""
    objects: list[CanvasObject] = []        # 当前画布对象集（rid + 类型 + 摘要属性）
    view: Literal["graph", "map", "split"] = "graph"
    color_by: str | None = None             # 着色属性 api_name
    expanded_links: list[str] = []          # 已展开的 link api_name
    object_count: int = 0
    last_query_summary: str = ""            # 上一步查询摘要，Agent 决策依据（如 "Dealership (0 个对象)"）
```

机制：
- **工具写 state**：后端数据工具返回 `StateSnapshotEvent(snapshot={"canvas": canvas.model_dump()})`，pydantic-ai 作为事件流推给前端。
- **Agent 读 state**：`@agent.instructions` 读 `ctx.deps.state.canvas`，每轮看到当前画布状态（含 `last_query_summary` / `object_count`）。这是 ReAct 的「当前状态」感知通道——`object_count=0` 时 Agent 自然终止，不需要空结果守卫规则。
- **前端订阅**：`useAgent` hook 订阅 state 变化 → 调 `explore.loadStartSet` / `setView` / `setLayerStyle` 同步画布。双向：前端画布操作（如手动右键 search around）也可写 state 回传 Agent。
- **`dispatch_request` 自动注入**：`AGUIAdapter.dispatch_request` 从请求体把前端 state 注入 `deps.state`，实现双向同步。

### D4: 废弃 `explore-plan` 与关键词路由

- 删除 `POST /objects/{ont}/explore-plan` 路由、`services/textql/explore_plan_parser.py`、`core/schemas/explore_plan.py`、前端 `hooks/usePlanExecutor.ts`、`api/graph.ts` 的 `explorePlan`。
- 删除 `should_route_to_object_set` 关键词列表（含 2026-07-04 临时补的「地理分布」补丁）。
- `query-nl` 路由：**最终未保留（范式对齐修订）**。ADR-015 初版 D4 曾设想「保留 query-nl 作无 Agent 的低延迟单步入口」，但实现时删得更彻底，且经 Palantir 范式调研确认这是正确决策——对齐 Palantir Foundry 两层正交架构：Ontology REST API（OSDK `search`/`loadObjectSet`）**从不接受自然语言**，NL→ObjectSet 转换全部在 AIP Agent 层（Object Query Tool，LLM tool calling 调 OSDK）。Gaia 对应：层 1 `/objects/*` 只吃结构化 ObjectSet IR（对应 OSDK search），层 2 `/ai/agent` 的 `query_with_dataframe` 工具是 LLM 调用（对应 Object Query Tool）。脚本/外部 Agent 的 NL 查询走 MCP `query_with_dataframe` 工具或 `/ai/agent`，**不在 `/objects/*` 加 NL 端点**。此约束已固化为 CLAUDE.md 红线 11，避免日后重蹈「为每个消费者场景开端点」的功能思维覆辙。
- 「分析/评估/排查/挖掘」类多步问题全部走 `/ai/agent`（AG-UI Agent），不再走 `explore-plan`。
- 同步更新 `graph-reasoning-frontend-design-v3.md`，标注其 §explore-plan 机制被本 ADR 取代。

### D5: ReAct 自然止损 — 不需要空结果守卫

这是本 ADR 的核心收益。旧 `explore-plan` 在 0 对象时空转 5 步编结论，根因是「编排器看不到中间结果」。新方案：

```
Agent 第1步：调 query_with_dataframe(Dealership)
  → 后端返回 0 对象 → 写 state {object_count: 0, last_query_summary: "Dealership (0 个对象)"}
Agent 第2步：读 state 看到 object_count=0
  → Agent 自行判断「本体无相关数据」→ TEXT_MESSAGE 告知用户无法分析 + 建议
  → 终止，不调用 switch_view/color_by，不编造结论
```

ReAct 的 observe（看 state）→ think（判断无法继续）→ act（如实告知）天然止损。**无需任何 if 守卫规则**——这正是「AI 时代处理方式」对「规则时代空结果检查」的替代。

同理解决「地理分布」误拒：Agent 自行决定调 `query_with_dataframe` + `switch_view("map")`，**不需要 `should_route_to_object_set` 关键词路由**。

## 替代方案

| 方案 | 否决理由 |
|------|---------|
| 保留 `explore-plan` 改单步循环（前端当状态机） | 伪 ReAct，后端无状态，仍独立于 AG-UI Agent，重复轮子 |
| 保留 `explore-plan` 加空结果守卫 if | 规则时代补丁，治标不治本，仍是一次性编排思维 |
| 画布操控工具全放后端 toolset | 违反 AG-UI 官方 frontend/backend tool 划分；MCP/REST 入口暴露无意义工具 |
| 用 CUSTOM 事件驱动画布 | 非一等公民，无 schema 约束，无 HITL 复用；STATE_SNAPSHOT 是 shared state 标准通道 |
| 用 Declarative (A2UI) 让 Agent 发画布 schema | 画布是固定 UI 非即兴生成，Controlled 更合适；A2UI 适合大量卡片场景 |
| 全部走 `query-nl` 不用 Agent | 单步 IR 无法处理「分析类」多步推理，且无状态反馈 |

## 实现范围

### 后端

| 文件 | 改动 |
|------|------|
| `tools/state.py` | `AppState` 改为 `StateDeps[CanvasSnapshot]` 或组合（保留 `executor`/`ontology`/`injected_schema`，新增 `canvas: CanvasSnapshot`）。需兼容 write/action 工具的 `RunContext[AppState]`。 |
| `core/schemas/canvas.py` | **新增**。`CanvasSnapshot` + `CanvasObject` schema。 |
| `services/ai_agent.py` | `deps_type=StateDeps[AppState]`（或包装）；`@agent.instructions` 读 `ctx.deps.state.canvas` 注入画布状态；reasoning/link_traversal 工具返回 `StateSnapshotEvent` 写画布 state。 |
| `routes/ai.py` | `fresh_deps` 初始化 `CanvasSnapshot`；`dispatch_request` 已自动处理 state 双向同步。 |
| `routes/query/__init__.py` | 删 `explore-plan` 路由；**`query-nl` 路由整体删除**（D4 修订：最终未保留，对齐 Palantir 两层正交，见上）。 |
| `services/textql/explore_plan_parser.py` | **删除**。 |
| `core/schemas/explore_plan.py` | **删除**。 |
| `services/textql/object_set_parser.py` | 删 `should_route_to_object_set` + `_GRAPH/_SPATIAL/_TEMPORAL_KEYWORDS`。 |

### 前端

| 文件 | 改动 |
|------|------|
| `hooks/useGraphExploreAgent.ts` | **新增**。封装 AG-UI runtime（复用 `ScopedHttpAgent` 模式）+ state 订阅桥接 `useGraphExplore`（state.canvas → loadStartSet/setView/setLayerStyle）。注册前端 tools（switch_view/color_by）。 |
| `pages/GraphExplorePage.tsx` | exploring 模式左侧对话流接 AG-UI runtime；删 `usePlanExecutor`/`explorePlan`；landing 的 `runNLQuery` 分析类问题走 AG-UI Agent。 |
| `hooks/usePlanExecutor.ts` | **删除**。 |
| `api/graph.ts` | 删 `explorePlan`。 |
| `types/canvas.ts` | **新增**。`CanvasSnapshot` TS 类型（与后端 snake_case 对齐）。 |

### 测试（CLAUDE.md 要求）

- `tests/unit/tools/test_canvas_state.py` — `CanvasSnapshot` 序列化 + 工具返回 `StateSnapshotEvent` 写 state 验证。
- `tests/unit/services/test_ai_agent_canvas.py` — Agent instructions 读画布 state + 0 对象自然终止的端到端（mock LLM 验证 ReAct 步骤）。
- 删 `explore-plan` 相关测试；更新 `query-nl` 路由测试（删 NOT_REASONING_QUERY 断言）。
- 前端 `useGraphExploreAgent` 工具桥接单测。

## 后续工作

| 项 | 阶段 | 说明 |
|----|------|------|
| `switch_view`/`color_by` 迁移为真正 frontend-defined tool | Sprint 2 | pydantic-ai 2.0 `AGUIAdapter` 原生封装 `_AGUIFrontendToolset`，前端通过 `RunAgentInput.tools` 注册工具 + 处理 `TOOL_CALL_*` 生命周期即可。迁移后 MCP/REST 入口不再暴露这两个纯 UI 工具，对齐 ADR-015 D2 原始划分 |
| `canvas_context` 增强 | Sprint 2 | AppState 主动暴露画布节点类型分布、已加载类型，让 Agent 决策更准（MVP 靠 `last_query_summary` 足够） |
| 前端画布操作回写 state | Sprint 2 | 用户手动右键 search around / 框选过滤也写 state 回传 Agent（双向闭环） |
| 多轮探索上下文持久化 | Sprint 3 | thread 级 state 持久化，刷新页面恢复画布 |
| Declarative Gen UI 评估 | 远期 | 若图探索衍生大量结果卡片（如路径分析结果可视化），评估 A2UI |

## 参考

- [pydantic-ai AG-UI shared state 官方示例](https://github.com/pydantic/pydantic-ai/blob/main/examples/pydantic_ai_examples/ag_ui/api/shared_state.py) — `StateDeps[RecipeSnapshot]` + 工具返回 `StateSnapshotEvent` 的权威模板
- [AG-UI Protocol — State Management](https://docs.ag-ui.com/concepts/state) — STATE_SNAPSHOT/STATE_DELTA 双向同步机制
- [AG-UI Protocol — Tools](https://docs.ag-ui.com/concepts/tools) — frontend-defined vs backend-defined tools 划分
- [CopilotKit — Render agent state in your app](https://docs.showcase.copilotkit.ai/google-adk/shared-state/rendering-in-app) — agent.state 驱动 canvas/dashboard 模式
- [Agent Cookbook — Generative UI Is the New Frontend](https://agent-cookbook.com/tutorial/generative-ui-is-the-new-frontend) — 三种 Gen UI 模式决策树 + shared state 取舍判据
- [Palantir AIP — Application state](https://palantir.com/docs/foundry/chatbot-studio/application-state/) — Agent 通过 application variables 驱动 dashboard 的范式源头
- [adr-009-ontology-tool-layer.md](./adr-009-ontology-tool-layer.md) — 本体工具层（后端 toolset 复用）
- [adr-010-ontology-hitl.md](./adr-010-ontology-hitl.md) — HITL 审批（前端 tool 同样可接 MetadataApprovalToolset）
