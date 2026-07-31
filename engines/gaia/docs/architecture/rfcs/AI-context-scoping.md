# RFC: AI 助手上下文范围约束（当前本体隔离）

> 状态：**待评审**  
> 作者：架构 / 首席工程评审  
> 日期：2026-06-23  
> 关联：ADR-009（AI 工具层）、ADR-010（HITL）、`docs/engineer/ai-integration-guide.md`

## 0. TL;DR

用户在「测试本体」工作区打开 AI 助手问"列出所有本体"，AI 调用 `list_ontologies` 把系统里 56 个本体（含大量 E2E 测试本体）全列出来。**这是 bug**：助手应感知"用户当前所在本体"，默认只在该本体内操作。

根因是**上下文断层**：前端知道 `selectedOntology`，但没有传给后端；后端 `AppState`（AG-UI deps）没有该字段；read-only 工具用 `tool_plain`（无 `ctx`）拿不到 deps，`list_ontologies` 无 ontology 参数天然全量返回。

本 RFC 给出 3 个候选方案，推荐 **方案 B**，但需评审拍板。**未评审通过前不动代码。**

---

## 1. 问题事实核查

### 1.1 当前数据流

```
前端 OntologyWorkspace
  └─ AiSuggestPanel (静态 systemPrompt = ONTOLOGY_QUERY)
       └─ AssistantUiChat → HttpAgent(url='/ai/agent')
            └─ POST /ai/agent  (RunAgentInput: thread_id, messages, state=[], ...)
                  └─ AGUIAdapter.dispatch_request(agent, deps=AppState(thread_id, executor))
                       └─ Agent.run(toolsets=[read-only + write/action])
```

### 1.2 断层点（已逐项核对源码）

| # | 位置 | 事实 | 后果 |
|---|------|------|------|
| F1 | `src/web-ui/src/components/AiSuggestPanel.tsx` | `<AssistantUiChat systemPrompt={ONTOLOGY_QUERY} />`，未传 `selectedOntology` | 前端知道本体但丢弃 |
| F2 | `src/web-ui/src/api/prompts.ts` `ONTOLOGY_QUERY` | 静态文本，无当前本体占位 | LLM 不知道用户在哪个本体 |
| F3 | `src/ontology/tools/state.py` `AppState` | 仅 `thread_id / executor / recent_calls`，无 `ontology` | deps 不携带上下文 |
| F4 | `src/ontology/routes/ai.py` `ag_ui_stream` | 只从 body 读 `thread_id`，未读 `state` / ontology | route 不提取上下文 |
| F5 | `src/ontology/tools/toolsets/metadata.py` 等 | `@ts.tool_plain`（无 ctx），`list_ontologies()` 无参数 | 工具层无法兜底，天然全量 |
| F6 | MCP 路径 `mcp_server.py::_register_readonly_tools` | 直接复用 `builder(executor).tools.values()` 的 `tool.function` 注册给 FastMCP | **read-only 工具是 AG-UI / MCP 共用同一函数对象，改签名会同时影响两条路径** |

### 1.3 AG-UI 协议侧事实

- `RunAgentInput.state: Any`（自由结构字典）是前端→后端输入；`AGUIAdapter.state` 读取它，但**不会自动注入到 `ctx.deps`**——deps 由 route 层 `fresh_deps()` 构造。
- `STATE_SNAPSHOT` / `STATE_DELTA` 事件是后端→前端的方向（回传 state 给 UI），不是注入 deps 的机制。
- `dispatch_request` 支持 `instructions` 参数（`str | callable(ctx)->str`），是 per-run 动态 system prompt 的官方入口。
- `manage_system_prompt='client'` 下，agent 配置的 `system_prompt` 不注入，前端发的 system message 被保留；`instructions` 仍会叠加注入。

### 1.4 pydantic-ai toolset 事实

- `tool_plain`：无 ctx，schema 仅含业务参数。
- `tool`：首参 `RunContext[DepsT]`，pydantic-ai 在调用时自动注入，**不暴露给 LLM 的 tool schema**。
- write/action toolset 已是 `@ts.tool` + `RunContext[AppState]`，且与 MCP 共享 `*_logic` 函数（MCP 侧手写 `@mcp.tool` wrapper 取 fastmcp `Context`，再调 logic）。**这是已验证的"双路径共享 logic"范式。**

---

## 2. 设计目标与非目标

### 目标
- G1：AI 助手默认只在"用户当前打开的本体"内操作，不主动枚举/触碰其他本体。
- G2：`list_ontologies` 在有当前本体时被收敛（只返回当前本体，或完全禁用）。
- G3：用户显式问"系统有哪些本体"这类跨本体问题时，行为可预测、可解释。
- G4：MCP 路径（外部 Agent）行为不变——外部 Agent 没有"当前本体"概念，必须显式传 ontology。
- G5：向后兼容，不破坏现有 API 契约和已通过的测试。

### 非目标
- N1：不做多租户/数据权限隔离（这是 ActionAuth 的职责，本轮只约束 LLM 行为范围）。
- N2：不重构 AG-UI state 双向同步机制。
- N3：不引入 per-request agent 实例化（保持 agent 单例）。

---

## 3. 候选方案

### 方案 A：纯 Prompt 约束（最轻）

**做法**：前端把 `selectedOntology` 拼进 system prompt；后端不改。

```
ONTOLOGY_QUERY(ontology) = `... 你当前在本体「{ontology}」中工作，
所有查询默认限制在此本体；除非用户明确要求跨本体，不要调用 list_ontologies。`
```

- 改动：仅 `AiSuggestPanel.tsx`（传 ontology）+ `prompts.ts`（参数化）。
- **优点**：零后端改动，零 MCP 影响，1 人时。
- **缺点**：LLM 约束是"软"的——用户说"列出所有本体"仍可能触发 `list_ontologies` 全量返回（实测 DeepSeek 会遵守，但非强保证）；prompt 注入风险；G2 不满足（工具层仍能全量）。
- **评审点**：是否接受"软约束"？我认为对 read-only 场景可接受，但你提的 bug 本质是"能查出来"，软约束挡不住恶意/固执的 prompt。

### 方案 B：上下文透传 + 工具层硬约束（推荐）

**核心思路**：前端把 `selectedOntology` 透传到后端 `AppState.ontology`，read-only 工具改用 `RunContext[AppState]` 读取它，`list_ontologies` 在有当前本体时硬过滤，其他工具 ontology 参数改可选并兜底。MCP 路径通过"共享 logic + 双路径 wrapper"范式保持不变。

#### B.1 数据流

```
前端: state = { ontology: selectedOntology }  (RunAgentInput.state)
      + systemPrompt 注入当前本体（软约束，辅助）
后端 route: 从 body.state 读 ontology → fresh_deps(thread_id, ontology)
      → AppState.ontology
工具: @ts.tool + ctx: RunContext[AppState]
      list_ontologies: if ctx.deps.ontology: 过滤为 [当前]
      其他: ontology = ontology or ctx.deps.ontology
```

#### B.2 后端改动（5 文件）

1. **`state.py`**：`AppState` 加 `ontology: str = ""`（+注释说明 AG-UI/MCP 语义差异）。
2. **`ai_agent.py`**：`fresh_deps(thread_id="", ontology="")` 透传。
3. **`routes/ai.py`**：从 `body.get("state")` 读 `ontology`（兼容 `state` 为 dict / 已有 `forwardedProps`）；传给 `fresh_deps`。
4. **`toolsets/metadata.py` / `object_query.py` / `link_traversal.py`**：`tool_plain`→`tool`，加 `ctx: RunContext[AppState]`，`ontology: str` → `ontology: str = ""`，函数首行 `ontology = ontology or ctx.deps.ontology`；`list_ontologies` 特殊处理（过滤）。

#### B.3 MCP 路径适配（关键，决定方案可行性）

**问题**：F6——MCP 直接复用 `tool.function`，改成 `@ts.tool` 后 `tool.function` 首参是 `RunContext[AppState]`，FastMCP 会把它当业务参数暴露给 LLM。

**解法**：照搬 write/action 已有范式——**提取 `*_logic` 共享函数，双路径各自 wrapper**：

```python
# toolsets/metadata.py
async def list_object_types_logic(executor, ontology: str) -> list[dict]: ...  # 纯逻辑

def build_metadata_toolset(executor) -> FunctionToolset[AppState]:  # AG-UI 路径
    ts = FunctionToolset[AppState]()
    @ts.tool
    async def list_object_types(ctx: RunContext[AppState], ontology: str = ""):
        return await list_object_types_logic(executor, ontology or ctx.deps.ontology)
    return ts

# mcp_server.py  # MCP 路径（手写 wrapper，无 ctx.deps 兜底，ontology 必填）
@mcp.tool
async def list_object_types(ontology: str) -> Any:
    return await list_object_types_logic(ToolExecutor(container), ontology)
```

- **MCP 行为不变**：ontology 仍是必填参数，外部 Agent 显式传值，无"当前本体"概念。
- **代价**：read-only 13 个工具要拆 logic + 双 wrapper，改动量较大但机械、有现成范式。

#### B.4 前端改动（2 文件）

1. **`AssistantUiChat.tsx`**：接收 `ontology` prop，通过 AG-UI `state` 携带（需确认 `useAgUiRuntime` / `HttpAgent` 如何注入初始 state——见 §6 风险 R2）。
2. **`AiSuggestPanel.tsx`**：传 `ontology={selectedOntology}`，system prompt 参数化（软约束叠加）。

#### B.5 优点
- G1/G2 满足：工具层硬过滤，LLM 绕不过。
- G4 满足：MCP 行为不变。
- 与 write/action 范式一致，架构对称。

#### B.6 缺点 / 代价
- 改动面：后端 5 文件 + 前端 2 文件，read-only 13 工具拆 logic。
- 仍需 prompt 软约束配合（否则 LLM 可能反复试 `list_ontologies` 拿到只 1 条后困惑）。

### 方案 C：route 层 `instructions` 动态注入（折中）

**做法**：不改工具签名，在 `dispatch_request(instructions=...)` 注入 per-run 约束："当前本体是 X，只在此范围操作"。`list_ontologies` 不动（仍能全量，但 prompt 告诉 LLM 别调）。

- 改动：`routes/ai.py`（读 ontology + 构造 instructions）+ 前端透传 ontology。
- **优点**：不动 toolset，零 MCP 影响，比 A 强（per-run 注入，不依赖前端拼 prompt）。
- **缺点**：本质仍是软约束，G2 不满足；`instructions` 在 `manage_system_prompt='client'` 下与前端 system message 叠加，优先级/冲突需验证。
- **评审点**：可作为 A、B 的退路或过渡。

---

## 4. 方案对比矩阵

| 维度 | A 纯 Prompt | B 透传+硬约束 | C instructions |
|------|-------------|---------------|----------------|
| 满足 G1（默认本体内） | 软 | **硬** | 软 |
| 满足 G2（list 收敛） | 否 | **是** | 否 |
| G4 MCP 不变 | 是 | 是（需拆 logic） | 是 |
| 改动面（文件） | 2 | 7 | 3 |
| 改动面（工具签名） | 0 | 13 | 0 |
| 架构对称性（与 write/action 一致） | 否 | **是** | 否 |
| 注入风险 | 中（prompt 拼接） | 低 | 中 |
| 可回归测试性 | 弱 | **强**（工具层可单测） | 弱 |

---

## 5. 推荐：方案 B

**理由**：
1. 你提的 bug 是"能查出来"——软约束（A/C）挡不住，只有工具层硬约束（B）能根治。
2. B 的 MCP 适配有 write/action 已验证的范式，风险可控。
3. 工具层硬约束可写单元测试（mock `AppState.ontology`，断言 `list_ontologies` 只返回 1 条），回归性强。
4. 架构对称：read-only 与 write/action 都走"共享 logic + 双路径 wrapper"，降低长期认知成本。

**不推荐 A**：对 read-only 尚可，但治标不治本，且未来加 write 范围约束时还得重做。
**不推荐 C 单独用**：可作为 B 的 prompt 层补充，但不该是主方案。

---

## 6. 风险与待确认项

- **R1（B）**：13 个工具拆 `*_logic` 工作量。**缓解**：机械重构，可分批（先 metadata 4 个验证范式，再 object_query/link_traversal）。
- **R2（B/C）**：前端如何把 `ontology` 放进 `RunAgentInput.state`。`useAgUiRuntime` 无 `initialState` 选项，需确认是通过 `thread.reset({state})`、`HttpAgent` 选项、还是改 `forwardedProps`。**这是前端最大未知，需先做 spike 验证**。
- **R3（B）**：`routes/ai.py` 已 `await request.json()` 消费 body，`dispatch_request` 再读 body 是否失败。现状代码已这样跑（读 thread_id），说明 FastAPI/AGUIAdapter 能处理，但加 state 读取需复测。
- **R4（B）**：用户显式问"系统有哪些本体"时，硬过滤会只返回当前本体，可能反直觉。
  - **评审反馈后更新（推荐）**：已核实 AI 助手仅出现在 `OntologyWorkspace`（某本体内），无全局入口。因此 AI 始终处于某具体本体上下文，`list_ontologies` 在 AG-UI 路径无业务必要。推荐**AG-UI 路径不注册 `list_ontologies`**（根因消除），而非硬过滤或加逃生参数。MCP 路径（外部 Agent 无"当前本体"）仍保留该工具做本体发现。详见 §9。
- **R5**：`AppState.ontology` 进入 `STATE_SNAPSHOT` 回传前端，前端 state 结构需对齐（snake_case），否则双向同步告警。

---

## 7. 实施计划（待批准后执行）

1. **Spike（0.5d）**：验证 R2——前端如何注入 `RunAgentInput.state`。不通则方案 B 前端部分需调整（退到 forwardedProps 或 header）。
2. **后端（1d）**：按 B.2/B.3 改造，先 metadata 4 工具建立范式 + 单测，再批量化 object_query/link_traversal。
3. **前端（0.5d）**：B.4 透传 + prompt 参数化。
4. **联调（0.5d）**：浏览器实测"列出所有本体"只返回当前本体；MCP 路径回归（ontology 必填）。
5. **测试**：新增 `tests/unit/tools/test_context_scoping.py`；跑全量 `make test`。

---

## 8. 决策请求

请评审以下问题并拍板：

1. **选哪个方案？** A / B / C / 其他。
2. **R4 逃生参数**：改为推荐"AG-UI 路径不注册 `list_ontologies`"（见 §9），是否认可？
3. **R2 spike**：是否同意先做前端 state 注入的 spike 再全面实施？
4. **分批策略**：B 是否接受先改 metadata 4 工具验证范式，再推广？

---

## 9. 工具注册分层：AG-UI vs MCP（评审反馈）

### 9.1 背景

`list_ontologies` 是"发现"类工具，用于 Agent 初始定位。问题在于：AG-UI 路径下用户已经在某个本体工作区里，该工具只会成为越界枚举的入口。

### 9.2 事实

- 已核实：`AiSuggestPanel` 仅在 `OntologyWorkspace`（某本体内）挂载，**无全局 AI 入口**。即 AG-UI 路径下 AI 始终处于某具体本体上下文。
- MCP 路径服务外部 Agent（Cursor / Claude Desktop 等），无"当前本体"概念，**必须**靠 `list_ontologies` 发现可用本体后再操作。

### 9.3 推荐策略：按路径分层注册

| 工具 | AG-UI 路径（Web UI，有当前本体） | MCP 路径（外部 Agent，无当前本体） |
|------|-------------------|-------------------|
| `list_ontologies` | **不注册**（用户已选定本体，无业务必要） | 注册（本体发现入口，必填 ontology 参数链的起点） |
| `list_object_types` / `describe_object_type` / `describe_link_type` / `list_link_types` | 注册，ontology 参数可选（默认 `ctx.deps.ontology`） | 注册，ontology 参数必填 |
| object_query / link_traversal | 同上 | 同上 |

### 9.4 实现方式

沿用方案 B 的"共享 `*_logic` + 双路径 wrapper"范式：

```python
# toolsets/metadata.py — 共享 logic
async def list_ontologies_logic(executor) -> list[dict]: ...

def build_metadata_toolset(executor) -> FunctionToolset[AppState]:  # AG-UI 路径
    ts = FunctionToolset[AppState]()
    # 注意：不注册 list_ontologies
    @ts.tool
    async def list_object_types(ctx, ontology: str = ""):
        return await list_object_types_logic(executor, ontology or ctx.deps.ontology)
    ...
    return ts

# mcp_server.py — MCP 路径
@mcp.tool
async def list_ontologies() -> Any:  # MCP 独立注册，外部 Agent 发现用
    return await list_ontologies_logic(ToolExecutor(container))
```

### 9.5 优点

- **根因消除**：AI 拿不到 `list_ontologies`，无法枚举其他本体，比硬过滤更彻底。
- **MCP 不受影响**：外部 Agent 仍可发现本体，向后兼容。
- **简化方案 B**：不需要 `list_ontologies` 的硬过滤逻辑、不需要 `include_all` 逃生参数、不需要为它做 `ctx.deps.ontology` 兜底——少一个工具的特殊处理。
- **可测试**：断言 AG-UI toolset 的 tool 名单不含 `list_ontologies` 即可回归。

### 9.6 代价 / 注意

- `list_ontologies_logic` 仍需提取（MCP wrapper 要用），但 AG-UI 侧不注册，改动量比硬过滤更小。
- system prompt 需同步调整：AG-UI 路径的 prompt 不再提"先调 list_ontologies"，改为"你已在本体 X 中，直接用 list_object_types"。
