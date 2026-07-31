# pydantic-ai × AG-UI 集成指南

> Gaia 项目 AI 能力基座文档。记录架构设计、集成方案、踩坑经验,供后续 AI 功能演进参考。
>
> 版本:v3.0 | 日期:2026-06-18

---

## 一、架构设计

### 1.1 核心原则:AG-UI 协议统一前后端交互

v3.0 起,前后端所有 AI 交互**统一走 AG-UI 协议**(Agent-User Interaction Protocol)。
旧的 `POST /ai/stream` 薄代理端点(裸 `system_prompt`/`user_prompt` + 自定义 SSE 事件)已废除。

```
┌─────────────────────────────────────────────────────────┐
│  Frontend (React + assistant-ui)                        │
│                                                         │
│  @assistant-ui/react-ag-ui  - useAgUiRuntime            │
│  @ag-ui/client               - HttpAgent (SSE 客户端)    │
│                                                         │
│  · Thread / MessagePrimitive 自动渲染 AG-UI 标准事件     │
│  · 工具调用 UI / 人在回路审批 / 共享状态自动同步         │
│  · Prompt 模板仍在前端维护(通过 messages 注入)         │
└─────────────────────────────────────────────────────────┘
         │ POST /ai/agent  (AG-UI RunAgentInput)
         ▼
┌─────────────────────────────────────────────────────────┐
│  Backend (FastAPI + pydantic-ai)                        │
│                                                         │
│  routes/ai.py             - AGUIAdapter.dispatch_request │
│  services/ai_agent.py     - 有状态 Agent + 工具 + state  │
│                                                         │
│  pydantic_ai.ui.ag_ui.AGUIAdapter 把 Agent 推理         │
│  自动转换为 AG-UI 标准事件流(TEXT/TOOL/STATE/RUN)       │
└─────────────────────────────────────────────────────────┘
         │ pydantic-ai
         ▼
    DeepSeek / OpenAI / Claude / ...
```

### 1.2 为什么从薄代理升级到 AG-UI

| 维度 | v2.0 薄代理 `/ai/stream` | v3.0 AG-UI `/ai/agent` |
|------|------------------------|-------------------------------|
| 请求体 | `{system_prompt, user_prompt}` 裸字符串 | `RunAgentInput`(结构化:`thread_id`/`run_id`/`messages`/`state`/`tools`) |
| 流事件 | 自定义 `{type:partial\|result\|error}` | AG-UI 标准 `TEXT_MESSAGE_*`/`TOOL_CALL_*`/`STATE_*`/`RUN_*` |
| 多轮对话 | ❌ 每次一次性 | ✅ `thread_id` 串联会话历史 |
| 工具调用 | ❌ 无 | ✅ 工具生命周期事件 + 前端 generative UI |
| 人在回路 | ❌ 无 | ✅ `requires_approval` + `DeferredToolResults` 两段式审批 |
| 前后端状态同步 | ❌ 无 | ✅ `STATE_SNAPSHOT`/`STATE_DELTA` |
| 前端渲染 | 手写 SSE 解析 + `parseAiJson` | assistant-ui 标准组件自动渲染 |

### 1.3 数据流

```
用户在 assistant-ui Thread 输入 "汽车制造领域,车型对象"
  ↓ HttpAgent 封装 RunAgentInput POST /ai/agent
  ↓ FastAPI → AGUIAdapter.dispatch_request → Agent.run_stream
  ↓ Agent 流式输出文字 → TEXT_MESSAGE_CONTENT 逐 token 事件
  ↓ (可选) Agent 调工具 → TOOL_CALL_START / TOOL_CALL_END
  ↓ (可选) 工具 requires_approval → 前端渲染审批 UI → 用户提交
  ↓        → 下一轮 run 带 deferred_tool_results 恢复执行
  ↓ (可选) Agent 修改 ctx.deps.state → STATE_DELTA 增量同步前端
  ↓ RUN_FINISHED → 会话结束,thread_id 保留历史
```

---

## 二、后端 API

### 2.1 唯一端点

```
POST /ai/agent
Content-Type: application/json
Accept: text/event-stream

请求体: AG-UI 标准 RunAgentInput
{
  "thread_id": "uuid-7",
  "run_id": "uuid-7",
  "messages": [ { "role": "user", "content": "...", "id": "..." } ],
  "state": { ... },            // ag-ui-protocol 0.1.x 下为必填字段(可空 {})
  "tools": [ ... ],            // 必填(可空 [])
  "context": [ ... ],          // 必填(可空 [])
  "forwardedProps": { ... },   // 必填(可空 {})
  "deferred_tool_results": ... // 可选,人在回路恢复时携带
}
```

**响应**:AG-UI 标准 SSE 事件流

```
data: {"type":"RUN_STARTED","thread_id":"...","run_id":"..."}
data: {"type":"TEXT_MESSAGE_START","message_id":"...","role":"assistant"}
data: {"type":"TEXT_MESSAGE_CONTENT","message_id":"...","content":"[{"}
data: {"type":"TEXT_MESSAGE_CONTENT","message_id":"...","content":"\"api_name\":"}
...
data: {"type":"TEXT_MESSAGE_END","message_id":"..."}
data: {"type":"RUN_FINISHED","thread_id":"...","run_id":"..."}
```

异常:`RUN_ERROR` 事件携带 message。

### 2.2 后端实现

> ⚠️ **不要用** `pydantic_ai.ag_ui` 模块(已 deprecated,2.0 移除)。
> 用 `pydantic_ai.ui.ag_ui.AGUIAdapter`,导入路径见下。

```python
# services/ai_agent.py

from pydantic_ai import Agent, RunContext, ModelSettings
from pydantic import BaseModel

from ontology.config.settings import settings


class AppState(BaseModel):
    """前后端共享状态,AG-UI 自动下发 STATE_SNAPSHOT / STATE_DELTA。"""
    task_list: list[str] = []
    total_amount: float = 0.0


def build_agent() -> Agent:
    """构建 AG-UI Agent。system_prompt 由前端通过 messages 注入(manage_system_prompt='client')
    或在 Agent 上固定('server')。本项目默认 'client',保留前端控 prompt 的原则。"""
    return Agent(
        settings.ai_model,
        deps_type=AppState,
        system_prompt="",  # 留空,prompt 走 messages
        model_settings=ModelSettings(
            temperature=settings.ai_temperature,
            max_tokens=settings.ai_max_tokens,
        ),
    )


agent = build_agent()


@agent.tool
async def suggest_object_types(ctx: RunContext[AppState], domain: str) -> str:
    """本体建议工具。Agent 决定何时调用,结果作为 TOOL_CALL_RESULT 事件下发。"""
    # 工具内可读写 ctx.deps(共享状态),修改后自动产出 STATE_DELTA
    ctx.deps.task_list.append(f"已建议:{domain}")
    return f"为领域 {domain} 生成对象类型建议..."
```

```python
# routes/ai.py

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic_ai.ui.ag_ui import AGUIAdapter

from ontology.services.ai_agent import agent, AppState

router = APIRouter(prefix="/ai", tags=["ai"])


@router.post("/agent")
async def ag_ui_stream(request: Request) -> Response:
    """AG-UI 标准接入端点。

    AGUIAdapter.dispatch_request 自动完成:
    1. 校验 RunAgentInput 请求体(ag-ui-protocol 强类型)
    2. 执行 agent 推理,转换内部事件为 AG-UI 标准事件流
    3. 返回 SSE StreamingResponse(accept: text/event-stream)
    """
    return await AGUIAdapter.dispatch_request(
        request,
        agent=agent,
        deps=AppState(),
        manage_system_prompt="client",  # 前端通过 messages 拥有 prompt
    )
```

**关键点**:
- `AGUIAdapter.dispatch_request` 是官方当前 API,替代已 deprecated 的 `handle_ag_ui_request`/`run_ag_ui`。
- `manage_system_prompt='client'` 保留"前端拥有 prompt"原则:system_prompt 作为 `messages` 里的 system 角色消息由前端注入。
- `deps=AppState()` 提供共享状态;Agent 工具内修改 `ctx.deps` 自动产出 `STATE_DELTA` 同步前端。
- 若需要服务端固定 system_prompt(解锁更强 Agent 能力),改 `manage_system_prompt='server'` 并在 `build_agent()` 里写 `system_prompt`。

### 2.3 人在回路(HITL)

**机制**(对齐 pydantic-ai 源码 `tools.py`):工具声明 `requires_approval=True`,Agent 调用时**不立即执行**,run 结束并产出 `DeferredToolRequests`;前端渲染审批 UI;用户决策后,**下一轮 run** 携带 `deferred_tool_results` 恢复执行。

```python
@agent.tool(requires_approval=True)
async def apply_transaction(ctx: RunContext[AppState], amount: float) -> str:
    """需要人工审批的交易工具。"""
    ctx.deps.total_amount += amount
    ctx.deps.task_list.append(f"已审批交易:{amount}")
    return f"交易 {amount} 已执行"
```

> ⚠️ **不存在的 API(别用)**:`ctx.ui_event()`、`ctx.wait_for_human_input()` 在当前 pydantic-ai 版本里**根本不存在**(RunContext 无此方法)。参考资料里的"阻塞等表单"写法是虚构的。真实流程是两段式 run,不是单次阻塞。

#### 版本限制(重要)

HITL 的"前端审批 → 自动回传 → 后端恢复"闭环,依赖两个较新的能力:

1. **pydantic-ai PR #5441**:`AGUIAdapter` 把 `DeferredToolRequests.approvals` 映射为 `RUN_FINISHED` 的 `outcome=interrupt` 事件,并把入站 `resume[]` 转回 `DeferredToolResults`。
2. **ag-ui-protocol PR #1569**:interrupt-aware run lifecycle。
3. **assistant-ui**:`ToolFallback.Approval` 内置 Allow/Deny(PR #4229)或自定义渲染器调 `respondToApproval`。

**本项目当前装的 pydantic-ai 1.107.0 源码里不含 #5441 的代码**(已核实:`ui/ag_ui/` 下无 `RunFinishedInterruptOutcome`/`resume`/`outcome` 相关实现)。因此在当前版本下:

- 后端 `requires_approval` 工具能产出 `DeferredToolRequests`,但 **AG-UI adapter 不会自动发 interrupt 事件**;
- 前端 **无法**通过标准 AG-UI 事件获知"待审批"状态并自动恢复。

**结论:1.107.0 下彻底放弃 pydantic-ai 的 `requires_approval` 机制,HITL 走纯业务层中断。**

#### 为什么 1.107.0 上 HITL 是"伪可运行"

1. **无私开 message 转换器**:`AGUIAdapter` 内部把 ag-ui `Message` 转 pydantic-ai `ModelMessage` 的逻辑(`_user_content_to_input` 等)全是 `_` 开头私有,`ui/ag_ui/__init__.py` 的 `__all__` 不导出任何转换函数。手动 HITL 端点要自行写 AG-UI `Message` → `ModelMessage` 转换,工作量大且易错。
2. **恢复路径不可靠**:即使手写转换器并用 `agent.run(output_type=DeferredToolRequests)` 拿到阻断对象,在第二段 run 用 `deferred_tool_results` 恢复时,1.107.0 缺 #5441 的 Tool Call ID 映射与 interrupt lifecycle 处理,存在模型上下文不匹配的风险。

#### 推荐替代:纯业务层中断(不走 pydantic-ai 审批机制)

- 后端工具**不加** `requires_approval=True`,正常执行;检测到需审批时,修改 `ctx.deps` 状态并返回标志性 JSON,如 `{"status":"NEED_APPROVAL","action_id":123}`;
- 前端通过 `Tools` toolkit 的 `render` 渲染该结果,弹窗让用户审批;
- 审批通过后,前端调一个**独立常规业务端点**(如 `POST /ai/action/confirm`)执行真实操作,完成后刷新 `AppState`(下轮 AG-UI run 的 `STATE_SNAPSHOT` 会同步)。

这样 HITL 完全在业务层闭环,不依赖 pydantic-ai 的 deferred tool 机制,1.107.0 可稳定运行。升级 pydantic-ai 到含 #5441 的版本后,再切回标准 `requires_approval` + assistant-ui 内置 `ToolFallback.Approval`(assistant-ui 侧 PR #3974 已在 `AgUiThreadRuntimeCore.installResumeShim()` 准备了 interrupt 处理)。

> ⚠️ **别用的虚构 API**(1.107.0 源码核实均不存在):
> - `result.new_forward_tool_requests()` -- `AgentRunResult` 无此方法
> - `result.stream_to_ag_ui_protocol()` -- `AgentRunResult` 无任何 `stream`/`ag_ui` 方法
> - `req.requires_approval` -- `DeferredToolRequests` 是 dataclass,只有 `calls`/`approvals`/`metadata` 字段
> - **`result.deps`** -- `AgentRunResult` **无 `deps` 属性**(公开方法仅 `all_messages`/`new_messages`/`usage`/`response`/`conversation_id` 等)。共享状态需在 `agent.run()` 调用前自存 `deps` 引用。
> - `c.id` / `c.name` -- `DeferredToolRequests.calls` 元素是 `ToolCallPart`,字段为 **`tool_name`** / **`tool_call_id`**(不是 `name`/`id`)。

**结论**:

- **1.107.0 当前版本**:HITL **不走** `requires_approval`,采用上文“纯业务层中断”方案(`NEED_APPROVAL` JSON + 独立 confirm 端点),可稳定运行。
- **升级路径**:pydantic-ai 升级到含 #5441、ag-ui-protocol 含 #1569 后,切回标准 `requires_approval=True` + assistant-ui 内置 `ToolFallback.Approval`(assistant-ui #3974 已备 `installResumeShim`),前端零定制。

> 此条已从“未确认项”转为**已定方案**(见 8.4)。

### 2.4 依赖

```bash
# 后端:必须装 ag-ui extra(提供 ag-ui-protocol + starlette)
uv add "pydantic-ai-slim[ag-ui]" fastapi uvicorn python-dotenv
```

不装 `ag-ui` extra 时,`from pydantic_ai.ui.ag_ui import AGUIAdapter` 会抛 `ImportError: Please install the ag-ui-protocol and starlette packages`。

### 2.5 跨域与鉴权(CORS / JWT)

SSE 长连接的跨域与鉴权是 `HttpAgent` 接入后最易卡壳处。

**后端 FastAPI CORS**:

```python
# main.py
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # 生产环境替换为具体 Web-UI 域名,勿用 *
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Accel-Buffering"],  # 防止 Nginx 缓存 SSE 流
)
```

**前端 JWT 透传**:`HttpAgent` 的 `headers` 只接受**静态对象**,不接受函数,也没有请求拦截器(ag-ui-protocol issue #1113)。动态 token 的可行做法:

```tsx
// 方案 A:每次新会话重建 runtime(拿到最新 token)
// runtime 重建后用 useState 换 runtime 对象(core.updateOptions 是内部 API,不可靠)
const makeAgent = () => new HttpAgent({
  url: "/ai/agent",
  headers: {
    Accept: "text/event-stream",
    Authorization: `Bearer ${localStorage.getItem("token") ?? ""}`,
  },
});
// 注意:不能在 thread.append 时换 agent(append 无此参数);需重建 runtime
// (core.updateOptions 是内部 API,不要用;改用 setState 换 runtime)
```

```tsx
// 方案 B(推荐):token 放进 RunAgentInput 业务字段,后端自取
// 前端通过 thread 的 forwardedProps 或 context 传 token,后端在 dispatch_request 前校验
```

> 不要写 `headers: () => ({...})` -- 会静默失败(headers 被当静态对象,函数不会被调用)。

---

## 三、前端集成

### 3.1 依赖

```bash
npm install @assistant-ui/react @assistant-ui/react-ag-ui @ag-ui/client
```

| 包 | 作用 |
|----|------|
| `@assistant-ui/react` | 核心(项目已装) |
| `@assistant-ui/react-ag-ui` | `useAgUiRuntime` 适配器 |
| `@ag-ui/client` | `HttpAgent` SSE 客户端 |

> 不要装 `@assistant-ui/styles` / `@assistant-ui/react-ui`(已废弃删除)。
> 不要装 CopilotKit--本方案用 assistant-ui 作为标准 AG-UI 消费者,不绑定 CopilotKit 私有的 `ui_render` 协议。

`Thread` 等预置 UI 组件不在 npm 包里,需用 shadcn registry 安装到本地源码(便于定制):

```bash
npx shadcn@latest add https://r.assistant-ui.com/thread.json
# 可选:多会话列表
npx shadcn@latest add https://r.assistant-ui.com/thread-list.json
# 可选:markdown 渲染
npx assistant-ui@latest add markdown-text
```
生成 `src/web-ui/src/components/assistant-ui/thread.tsx` 等。这些是本地源码,可自由改样式。

### 3.2 Runtime Provider

```tsx
// src/web-ui/src/components/AssistantUiChat.tsx
"use client";
import { useMemo } from "react";
import { AssistantRuntimeProvider, Tools, useAui } from "@assistant-ui/react";
import { Thread } from "@components/assistant-ui/thread";
import { HttpAgent } from "@ag-ui/client";
import { useAgUiRuntime } from "@assistant-ui/react-ag-ui";
import { ontologyToolkit } from "./assistant-ui/tools";

export function AssistantUiChat() {
  const agent = useMemo(
    () =>
      new HttpAgent({
        url: "/ai/agent",
        headers: {
          Accept: "text/event-stream",
          // Authorization: `Bearer ${localStorage.getItem("token") ?? ""}`,
        },
      }),
    [],
  );

  const runtime = useAgUiRuntime({
    agent,
    showThinking: true,
    onError: (e) => console.error("[ag-ui]", e),
  });

  // Tools({toolkit}) 返回 ResourceElement,传给 useAui 的 tools 作用域
  // (不能当 JSX 子元素渲染,见 §3.5)。
  const aui = useAui({ tools: Tools({ toolkit: ontologyToolkit }) });

  return (
    <AssistantRuntimeProvider runtime={runtime} aui={aui}>
      <Thread />
    </AssistantRuntimeProvider>
  );
}
```

`useAgUiRuntime` 自动消费全部 AG-UI 标准事件:

| 事件 | 前端行为 |
|------|---------|
| `RUN_STARTED` / `RUN_FINISHED` / `RUN_CANCELLED` / `RUN_ERROR` | 会话状态切换 |
| `TEXT_MESSAGE_START/CONTENT/END` | 逐 token 渲染 assistant 消息 |
| `THINKING_*` / `REASONING_*` | 推理过程折叠展示(`showThinking`) |
| `TOOL_CALL_START/ARGS/END/RESULT` | 工具调用进度 + 结果(generative UI) |
| `STATE_SNAPSHOT` / `STATE_DELTA` | 共享状态同步,`useAuiState` 读取 |
| `MESSAGES_SNAPSHOT` | 全量消息重建 |

### 3.3 Prompt 模板(前端维护)

prompt 仍由前端组装,作为 system 角色消息注入 `messages`:

```typescript
// src/web-ui/src/api/prompts.ts

export const ONTOLOGY_SUGGEST = `You are an ontology modelling expert...

Each ObjectType must have this exact structure:
{ "api_name": "...", "display_name": "...", ... }

Rules:
- Use DECIMAL for monetary amounts, TIMESTAMP for time
- Every object MUST have exactly one is_primary_key=true
- Output ONLY the JSON array. No markdown, no explanation.`;
```

新增场景加一个模板,在发送 user message 前先把对应 system prompt 作为 `messages[0]` 推给 `HttpAgent`(由 assistant-ui composer 或 `api.thread().append()` 注入)。

> ⚠️ **HttpAgent 不支持请求拦截器**:`@ag-ui/client` 的 `HttpAgent` 配置只有 `url` + `headers`(静态对象),**没有** `transformRequest` / 请求中间件选项(见 ag-ui-protocol issue #1113)。因此 system prompt 注入与动态 token 都不能靠拦截 HttpAgent。

**前端 Prompt 注入**:assistant-ui 的 `thread.append(message)` 签名只接受单个 message,**没有第二个 options 参数,也不能在 append 时换 agent**。正确做法:

- **system prompt**:thread 初始化时用 `runtime.thread().reset([{ role: "system", ... }, ...])` 预置 system 消息;或后端改 `manage_system_prompt='server'` 把 prompt 固定在 `build_agent()` 里(推荐,最稳)。
- **动态 token / 换 agent**:`useAgUiRuntime` 的 agent 是构造期绑定的。`AgUiThreadRuntimeCore.updateOptions` 虽存在于源码,但是 `react-ag-ui` 内部类,**外部无稳定途径拿到 core 实例**,不可靠。正确做法:重建整个 runtime(改 React `key` 触发 Provider 重挂,或 `useState` 换 runtime 对象);或把 token 放进 `RunAgentInput` 业务字段后端自取。**不能** `thread.append(payload, { agent })`(append 无第二参数)。

```tsx
// 动态 token 的可行写法(每次新会话重建 runtime)
const [runtime, setRuntime] = useState(() =>
  useAgUiRuntime({ agent: makeAgentWithCurrentToken() }),
);
// token 变化或新会话时: setRuntime(useAgUiRuntime({ agent: makeAgentWithCurrentToken() }))
```

### 3.4 读取共享状态

AG-UI 的 `STATE_SNAPSHOT` / `STATE_DELTA` 在 assistant-ui 里映射到 **`thread.state`**(`ReadonlyJSONValue`,即后端 `AppState` 的序列化形态)。读法:

```tsx
import { useMemo } from "react";
import { useAuiState } from "@assistant-ui/react";
import type { AppState } from "../api/types";

function StatePanel() {
  // thread.state 是后端 AppState 的镜像
  const state = useAuiState((s) => s.thread.state as AppState | undefined);
  // 防空:STATE_DELTA 在极快流式输出时 Patch 拼装可能有瞬态空窗
  const taskList = useMemo(() => state?.task_list ?? [], [state?.task_list]);
  const total = state?.total_amount ?? 0;
  return (
    <div>
      <p>总金额:{total}</p>
      <ul>{taskList.map((t, i) => <li key={i}>{t}</li>)}</ul>
    </div>
  );
}
```

后端 `ctx.deps` 变更 → `STATE_DELTA`(JSON Patch)→ `thread.state` 自动更新 → `useAuiState` 选择器触发重渲染。注意 `thread.state` 是不透明 JSON,需自行断言为 `AppState` 类型。

> ⚠️ 不要用 `s.shared` -- 该字段不存在。AG-UI state 统一落在 `s.thread.state`。
>
> ⚠️ **字段名必须 snake_case 对齐后端**:后端 pydantic 默认输出 snake_case(`task_list`/`total_amount`),前端 TS 类型**必须同名 snake_case**,写成 `taskList`/`totalAmount` 会拿不到值。`AppState` 类型声明:
> ```typescript
> // src/web-ui/src/api/types.ts
> export interface AppState {
>   task_list: string[];    // 与后端 pydantic AppState 同名,不可驼峰
>   total_amount: number;   // 同上
> }
> ```

### 3.5 工具调用 UI(generative UI)

> `makeAssistantToolUI` / `useAssistantToolUI` 已 **deprecated**。官方推荐用 **`Tools()` toolkit API**(在 tool 定义上挂 `render`)或 **`MessagePrimitive.Parts` 内联渲染**。下方采用 toolkit API。

后端 pydantic-ai `@agent.tool` 的执行在后端,前端只需为同名工具注册**渲染器**。这是 assistant-ui 的 **render-only backend tool** 模式:toolkit 条目声明 `type: "backend"` + 仅 `render`,**不带 `execute`、不带 `parameters`**(schema 与执行都在 AG-UI 后端)。

`toolName`(toolkit 的 key)必须与后端 `@agent.tool` 的函数名**完全一致**(大小写敏感):

```tsx
// src/web-ui/src/components/assistant-ui/tools.tsx
import { tool, type Toolkit } from "@assistant-ui/react";
import { ObjectTypeSuggestionCards } from "./ObjectTypeSuggestionCards";

// 后端工具:仅渲染,不执行(schema/execute 在 pydantic-ai 后端)。
// `tool()` 工厂是恒等函数,产出 canonical ToolDefinition;
// backend 形态要求 description/parameters/execute 全为 undefined,
// 因此**不要**在 backend tool 上写 description(会被类型拒绝)。
const ontologyToolkit = {
  suggest_object_types: tool({
    type: "backend",
    render: SuggestObjectTypesToolUI,  // ComponentType<ToolCallMessagePartProps>
  }),
} satisfies Toolkit;
```

> ⚠️ **`render` 是 React 组件,不是 render-prop 函数**:接收
> `ToolCallMessagePartProps`(`args` / `result` / `status` / `toolName` …)。
> 旧资料里的 `render: ({ args, result, status }) => <Comp/>` 是已废弃的
> `makeAssistantToolUI` 形态,toolkit API 下要写成组件。

在 Provider 内挂载——**注意:`Tools` 是 `@assistant-ui/tap` 的 Resource
工厂,返回 `ResourceElement`(`{hook,args}`),不是合法 React child,不能
当 JSX 元素渲染**。正确做法是传给 `useAui({ tools: Tools({ toolkit }) })`,
再把返回的 client 交给 `AssistantRuntimeProvider` 的 `aui` prop:

```tsx
import { AssistantRuntimeProvider, Tools, useAui } from "@assistant-ui/react";
import { ontologyToolkit } from "./assistant-ui/tools";

const aui = useAui({ tools: Tools({ toolkit: ontologyToolkit }) });

<AssistantRuntimeProvider runtime={runtime} aui={aui}>
  <Thread />
</AssistantRuntimeProvider>
```

> ⚠️ 本节原设计稿写 `<Tools toolkit={ontologyToolkit} />` 作为 Provider 的
> JSX 子元素——**实测在 assistant-ui 0.14.x 下会抛 "Objects are not valid
> as a React child (found: object with keys {hook, args})"**。`Tools({toolkit})`
> 必须经 `useAui({tools})` 挂载。见
> https://www.assistant-ui.com/docs/migrations/toolkit-tools。

> 工具的**执行**在后端(`@agent.tool`),前端 toolkit 只负责**渲染**工具调用过程与结果。toolkit 的 key 与后端工具名一一对应,assistant-ui 据此把 `TOOL_CALL_*` 事件匹配到对应渲染器。
>
> ⚠️ 后端工具不要写 `type: "frontend"` 或 `execute` -- 那是浏览器本地执行的工具(如剪贴板、localStorage)。frontend 工具可用 `frontendTools` 工具上传给后端,本场景不需要。

### 3.6 旧代码清理(迁移清单)

v2.0 → v3.0 必删/必改:

| 文件 | 操作 |
|------|------|
| `src/web-ui/src/api/client.ts` | **删除** `streamAiJson()` 和 `parseAiJson()`(AG-UI 不再手写 SSE 解析) |
| `src/web-ui/src/components/AiSuggestPanel.tsx` | **重写**:从手写流式面板改为 `AssistantUiChat` + `Tools` toolkit 渲染建议卡片;保留"应用建议 → 查重 → 创建"业务逻辑（**Sprint 2 后已改为 AG-UI Thread 多轮 + 写工具 HITL 批量审批**，见 commit 584af2c） |
| `src/web-ui/src/api/prompts.ts` | 保留,改由 assistant-ui messages 注入 |
| `src/ontology/services/ai_assistant.py` | **删除**(被 `ai_agent.py` 取代) |
| `src/ontology/routes/ai.py` | **重写**为 `/ai/agent` 端点 |
| `src/ontology/main.py` | router 注册不变(`ai_router` 仍挂 `/ai` 前缀) |

`AiSuggestPanel` 原有的滚动防抖、原始流折叠、部分 JSON 预解析等逻辑**不再需要**--assistant-ui 的 `Thread` 已内置逐字渲染与工具调用 UI(toolkit)。迁移时只保留业务侧的"建议 → 查重 → 批量创建"流程。

---

## 四、对象列表规模化

(与 AI 集成无关,沿用 v2.0 内容)

### 4.1 三种视图模式

| 模式 | 适用场景 | 特性 |
|------|---------|------|
| 📋 表格(默认) | 任意规模 | 搜索 + 列排序 + 分页(20条/页) |
| 🃏 卡片 | <20 直接渲染;≥20 可滚动 | 卡片式预览 |
| 🕸 图谱 | 全量 | Cytoscape.js 力导向布局 |

### 4.2 侧栏搜索

- >8 个对象时自动出现搜索框
- 输入即过滤(匹配 `display_name` 和 `api_name`)

### 4.3 图谱完善

- 创建边前校验 `source_object_type_id` / `target_object_type_id` 是否在当前节点集中
- 无效边跳过,避免 cytoscape 崩溃

---

## 五、配置

### 5.1 环境变量

```bash
# .env
AI_MODEL=deepseek:deepseek-v4-pro
DEEPSEEK_API_KEY=sk-xxxx
AI_TEMPERATURE=0.2
AI_MAX_TOKENS=16384
AI_RETRIES=2
```

### 5.2 Settings

```python
# config/settings.py

class Settings(BaseSettings):
    ai_model: str = "openai:gpt-4o"
    ai_temperature: float = 0.2
    ai_max_tokens: int = 16384
    ai_retries: int = 2
```

### 5.3 切换模型

改 `.env` 一行即可:

```bash
AI_MODEL=openai:gpt-5.2              # OpenAI
AI_MODEL=anthropic:claude-sonnet-4-6  # Claude
AI_MODEL=alibaba:qwen-max             # 通义千问
AI_MODEL=ollama:qwen3:14b             # 本地 Ollama
```

### 5.4 Provider 环境变量速查

| 前缀 | 环境变量 | 备注 |
|------|---------|------|
| `openai:` | `OPENAI_API_KEY` | |
| `deepseek:` | `DEEPSEEK_API_KEY` | 国内直连,性价比高 |
| `anthropic:` | `ANTHROPIC_API_KEY` | |
| `google:` | `GOOGLE_API_KEY` | |
| `google-cloud:` | `GOOGLE_CLOUD_API_KEY` | Vertex AI |
| `mistral:` | `MISTRAL_API_KEY` | |
| `moonshotai:` | `MOONSHOTAI_API_KEY` | Kimi |
| `alibaba:` | `ALIBABA_API_KEY` | 通义千问 |
| `grok:` / `xai:` | `XAI_API_KEY` | |
| `groq:` | `GROQ_API_KEY` | |
| `openrouter:` | `OPENROUTER_API_KEY` | |
| `fireworks:` | `FIREWORKS_API_KEY` | |
| `together:` | `TOGETHER_API_KEY` | |
| `ollama:` | 无需 | 本地 http://localhost:11434 |
| `bedrock:` | AWS 凭据 | IAM 或环境变量 |

---

## 六、安装与依赖

### 6.1 后端

```
pydantic-ai-slim  v1.107.x (本地源码安装,带 [ag-ui] extra)
ag-ui-protocol    (由 [ag-ui] extra 拉入)
starlette         (由 [ag-ui] extra 拉入)
openai            v2.41.1
```

```bash
# pydantic-ai 本地源码 + ag-ui extra(PyPI 版本 API 完全不同,必须本地源码)
uv add "/home/jason/code/pydantic-ai/pydantic_ai_slim[ag-ui]"
uv add openai fastapi uvicorn python-dotenv
```

### 6.2 前端

```bash
cd src/web-ui
npm install @assistant-ui/react-ag-ui @ag-ui/client
# @assistant-ui/react 已在项目中
```

### 6.3 Provider → SDK 映射

| Provider | 需要的 pip 包 |
|----------|-------------|
| `openai:`, `deepseek:`, `moonshotai:`, `alibaba:`, `groq:`, 等 | `openai` |
| `anthropic:` | `anthropic` |
| `google:`, `google-cloud:` | `google-genai` |
| `mistral:` | `mistralai` |
| `cohere:` | `cohere` |
| `bedrock:` | `boto3` |

---

## 七、踩坑记录

### 7.1 pydantic-ai 版本:PyPI vs 本地

**现象**:`ModuleNotFoundError: No module named '_griffe'`

**原因**:PyPI 版 `0.0.12` 与本地 `1.107.x` API 完全不同(`result_type` 参数位置、`stream_text` 方法等都不存在)

**解决**:必须从本地源码安装

### 7.2 API Key:pydantic-ai 读的是真实环境变量

**现象**:`.env` 里有 `DEEPSEEK_API_KEY`,但 pydantic-ai 报 `UserError: Set the DEEPSEEK_API_KEY environment variable`

**原因**:pydantic-ai 读 `os.environ`,不经过 pydantic-settings

**解决**:启动时显式 export

```bash
export DEEPSEEK_API_KEY=$(grep DEEPSEEK_API_KEY .env | cut -d= -f2)
uv run uvicorn ontology.main:app
```

### 7.3 AG-UI 模块导入:`pydantic_ai.ag_ui` 已 deprecated

**现象**:用 `from pydantic_ai.ag_ui import handle_ag_ui_request` 会抛 `PydanticAIDeprecationWarning`,官方明确 2.0 移除。

**解决**:改用新路径
```python
from pydantic_ai.ui.ag_ui import AGUIAdapter
# 调 AGUIAdapter.dispatch_request(request, agent=...)
```
不要用 `handle_ag_ui_request` / `run_ag_ui` / `agent.to_ag_ui()`。

### 7.4 AG-UI 依赖缺失

**现象**:`ImportError: Please install the ag-ui-protocol and starlette packages`

**原因**:`pydantic_ai.ui.ag_ui` 依赖 `ag-ui-protocol` + `starlette`,不装 `[ag-ui]` extra 无法导入。

**解决**:`uv add "pydantic-ai-slim[ag-ui]"`。

### 7.5 虚构的 HITL API

**现象**:参考资料称 `ctx.ui_event()` / `ctx.wait_for_human_input()` 可下发表单并阻塞等待。

**真相**:RunContext **没有**这两个方法。真实人在回路是工具级 `requires_approval=True` + 下一轮 run 带 `deferred_tool_results` 两段式恢复,不是单次阻塞调用。

**额外限制**:自动闭环需 pydantic-ai #5441 + ag-ui-protocol #1569 + assistant-ui #4229;当前 1.107.0 不含 #5441,详见 2.3 节与第八章。

### 7.6 ui_render 不是 AG-UI 标准事件

**现象**:参考资料把 `ui_render` / `human_request` 当 AG-UI 标准事件。

**真相**:这是 CopilotKit 私有扩展。AG-UI 标准事件只有 `TEXT_MESSAGE_*`/`TOOL_CALL_*`/`STATE_*`/`RUN_*` 等。本项目用 assistant-ui 标准消费,generative UI 走 `Tools()` toolkit 渲染 `TOOL_CALL_*` 事件,不碰 `ui_render`。

### 7.7 makeAssistantToolUI / useAssistantToolUI 已 deprecated

**现象**:按旧教程用 `makeAssistantToolUI({ toolName, render })` 注册工具 UI。

**真相**:官方已标记 deprecated(warn)。`makeAssistantTool`/`useAssistantTool`/`makeAssistantToolUI`/`useAssistantToolUI` 全部废弃。

**解决**:改用 `Tools()` toolkit API--在 `tool({ ... render })` 定义上挂渲染器,用 `<Tools toolkit={...} />` 挂载;或用 `MessagePrimitive.Parts` 内联渲染做 per-message UI。

### 7.8 AG-UI 共享状态读取路径

**现象**:用 `useAuiState((s) => s.shared?.xxx)` 读后端 `AppState`,拿不到值。

**真相**:`s.shared` 字段不存在。AG-UI `STATE_SNAPSHOT`/`STATE_DELTA` 在 assistant-ui 里映射到 **`thread.state`**(`ReadonlyJSONValue`)。

**解决**:`useAuiState((s) => s.thread.state as AppState | undefined)`。

### 7.9 prompt 约束 vs pydantic 约束

**现象**:LLM 输出缺少 `is_primary_key=true` 的属性

**原因**:pydantic `min_length=1` 在 Agent 中不生效

**解决**:在 prompt 中写 "Every ObjectType MUST have exactly one primary-key property"

### 7.10 cytoscape 边不存在导致崩溃

**现象**:`Can not create edge 'xxx' with nonexistent source 'yyy'`

**原因**:`links` 状态中有其他本体的旧数据

**解决**:创建边前校验 `source`/`target` 是否在当前节点集中

### 7.11 `manage_system_prompt='server'`(默认)会静默剥离前端 system 消息

**现象**:前端在 `messages` 里发了 system prompt,但 LLM 完全不遵守,日志出现 `UserWarning: Client-submitted system prompts were stripped because manage_system_prompt is 'server'`。

**原因**(源码 `ui/_adapter.py:371-413`):`manage_system_prompt` 默认 `'server'`,该模式下 `_sanitize_request_parts` 会**剥离所有 `SystemPromptPart`** 并 warn。前端 prompt 被丢掉,只有 `build_agent(system_prompt=...)` 里的生效。

**解决**:
- 要前端控 prompt → 显式设 `manage_system_prompt='client'`,此时前端 `SystemMessage` 被保留转 `SystemPromptPart`(**位置无关,不强制 messages[0]**);
- 要后端控 prompt → 保持默认 `'server'`,在 `build_agent()` 写 `system_prompt`,前端就别再发 system 消息。

> ⚠️ 文档示例用 `'client'` 保留前端控 prompt 原则,但**必须显式写**,漏写即默认 `'server'` 静默剥离。

### 7.12 `thread.reset()` 是公开 API,`core.updateOptions` 是内部 API

**现象**:网上资料混用 `thread.reset()` / `core.updateOptions({agent})`,不知哪些可调用。

**真相**(官方 ThreadRuntime 文档 + react-ag-ui 源码核实):
- `thread.reset(initialMessages?)` -- **ThreadRuntime 公开 API**(也存在于 AssistantRuntime / ThreadListRuntime),可安全调用,用于预置 system 消息或重置会话。
- `AgUiThreadRuntimeCore.updateOptions` -- 存在于源码,但是 `@assistant-ui/react-ag-ui` **内部类**,外部无稳定途径拿到 core 实例,**不要外部调用**。换 agent 应重建 runtime。
- `thread.append(msg, {agent})` -- **不存在**(append 只接受单个 message)。

**解决**:预置消息用 `thread.reset()`;换 agent/动态 token 用重建 runtime(改 React `key` 或 `useState` 换 runtime 对象)。

---

## 八、版本依赖与未确认项

落地前必须核实以下版本依赖,这是当前集成的主要风险点:

| 能力 | 依赖 | 当前状态 |
|------|------|----------|
| AG-UI adapter 基础 | `pydantic-ai-slim[ag-ui]` extra | ❌ 未装(需 `uv add`)|
| `AGUIAdapter.dispatch_request` | pydantic-ai ≥ 1.107 | ✅ 源码已含 |
| `manage_system_prompt` 参数 | pydantic-ai ≥ 1.107 | ✅ 源码已含 |
| 共享状态 `STATE_DELTA` | pydantic-ai `StateDeps` | ⚠️ 实测:adapter 只从 run input 读 `state`,**工具内修改 `ctx.deps` 不会自动产出 `STATE_DELTA`** |
| **HITL 自动闭环** | pydantic-ai #5441 + ag-ui-protocol #1569 + assistant-ui #4229 | ❌ **1.107.0 不含 #5441**(已核实源码)|
| `Tools()` toolkit API | assistant-ui 当前版 | ✅(替代 deprecated `makeAssistantToolUI`)|
| `thread.state` 读取 AG-UI state | assistant-ui `react-ag-ui` | ✅ |

### 建议起始版本(需实测锁定)

以下版本组合**未经项目内运行验证**,仅为建议起始点,落地时需实测锁定:

```json
// src/web-ui/package.json
{
  "dependencies": {
    "@assistant-ui/react": "^0.142.0",
    "@assistant-ui/react-ag-ui": "^0.5.0",
    "@ag-ui/client": "^0.3.0"
  }
}
```

```toml
# backend pyproject.toml / uv.lock
fastapi = "^0.111.0"
pydantic-ai-slim = { path = "/home/jason/code/pydantic-ai/pydantic_ai_slim", extras = ["ag-ui"] }
openai = "2.41.1"
```

### 未确认项(需运行验证)

1. **HITL**:见 2.3。**已定性**:1.107.0 下彻底放弃 pydantic-ai `requires_approval` 机制(无私开 message 转换器 + 恢复路径不可靠),HITL 走纯业务层中断(工具返回 `NEED_APPROVAL` JSON + 独立 `POST /ai/action/confirm` 端点)。升级 pydantic-ai 到含 #5441 后再切回标准机制(assistant-ui #3974 已备 `installResumeShim`)。此条从"未决"转为"已定方案"。
2. ~~`manage_system_prompt` 行为~~ → **已源码确认**(见 3.3 与 7.11):`'server'`(默认)剥离前端 system 消息并 warn;`'client'` 保留前端 system 消息(`SystemMessage` → `SystemPromptPart`,位置无关,不强制 messages[0])。注入靠 `thread.reset()`(ThreadRuntime 公开 API)预置或后端 `'server'`。换 agent 重建 runtime(`core.updateOptions` 是内部 API,不可靠),不能 `thread.append(msg,{agent})`。
3. ~~CORS / 鉴权透传~~ → **已澄清**(见 2.5):后端 CORS 标准;前端 HttpAgent headers 只接受静态对象,动态 JWT 需重建 runtime 换 agent,或走业务字段。
4. **assistant-ui / react-ag-ui / @ag-ui/client 三包版本兼容矩阵**:文档写作时未在项目内安装,版本组合需实测。

### 8.4 评审决议备案(2026-06-18)

- **鉴权与 Prompt 注入**:放弃在 `HttpAgent` 寻找中间件钩子(`transformRequest` 不存在、`headers` 不接受函数、`thread.append` 无 options 参数)。统一采用:**system prompt** 走 `thread.reset()` 预置或后端 `manage_system_prompt='server'`(源码确认 server 模式剥离+warn,client 保留);**动态 token** 走重建 runtime 换实例(`core.updateOptions` 是 react-ag-ui 内部 API,不外部用),或 token 放 `RunAgentInput` 业务字段后端自取。
- **HITL 定性**:1.107.0 下彻底放弃 pydantic-ai `requires_approval` 机制(源码核实:AGUIAdapter 无公开 message 转换器、缺 #5441 interrupt lifecycle,手动两段式为"伪可运行")。HITL 统一走纯业务层中断:工具返回 `NEED_APPROVAL` JSON + 前端 `Tools` toolkit 渲染弹窗 + 独立 `POST /ai/action/confirm` 端点执行。升级含 #5441 后再切回标准机制。
- **虚构 API 黑名单**(源码核实均不可用):`result.new_forward_tool_requests()`、`result.stream_to_ag_ui_protocol()`、`result.deps`、`req.requires_approval`、`HttpAgent.transformRequest`、`headers: () => ({...})`、`thread.append(msg, {agent})`、外部调 `core.updateOptions`(内部 API)。
- **版本矩阵**:文档所列前端三包版本为建议起始点,需实测锁定,不作为"已验证通过"。

## 九、后续演进

### 9.1 AI 场景扩展

新增 AI 能力 = 加一个 prompt 模板 + (可选)一个 `@agent.tool`。后端 Agent 自动通过 AG-UI 事件流驱动前端:

```python
@agent.tool
async def suggest_properties(ctx: RunContext[AppState], object_api_name: str) -> str: ...

@agent.tool
async def validate_schema(ctx: RunContext[AppState], object_types: list) -> str: ...
```

### 9.2 架构演进路线

```
v2.0          - 通用 JSON 流代理,前端组装 prompt(已废弃)
v3.0 (当前)   - AG-UI 协议统一,pydantic-ai Agent + assistant-ui
v3.1          - 工具调用 generative UI 全覆盖(本体建议/校验/映射)
v4.0          - Multi-Agent 协作(Planner + Builder + Reviewer,多 Agent 经 AG-UI 编排)
```

### 9.3 其他优化方向

- **会话持久化**:Redis 按 `thread_id` 存 `AppState` + `message_history`,重启不丢
- **鉴权**:FastAPI 全局校验 JWT,拒绝未授权 AG-UI 长连接
- **多模型 fallback**:主模型不可用时自动切换
- **语义缓存**:相似 business description → embedding 去重

---

## 十、快速参考

```bash
# 启动全部服务
cd /home/jason/code/gaia
export DEEPSEEK_API_KEY=$(grep DEEPSEEK_API_KEY .env | cut -d= -f2)
uv run uvicorn ontology.main:app --port 8000 --reload &
cd src/web-ui && npm run dev &

# 测试 AG-UI 端点(裸 RunAgentInput)
curl -N -X POST http://localhost:8000/ai/agent \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{"thread_id":"00000000-0000-7000-8000-000000000000",
       "run_id":"00000000-0000-7000-8000-000000000001",
       "messages":[{"role":"user","content":"汽车制造领域,车型对象","id":"m1"}]}'

# 切换模型:改 .env 的 AI_MODEL,重启后端
```

## 附录:核心文件索引

| 文件 | 职责 |
|------|------|
| `src/ontology/services/ai_agent.py` | AG-UI Agent + 工具 + AppState(v3.0 新增) |
| `src/ontology/routes/ai.py` | `POST /ai/agent`(AGUIAdapter.dispatch_request) |
| `src/ontology/config/settings.py` | AI 模型配置(ai_model + 参数) |
| `src/web-ui/src/components/AssistantUiChat.tsx` | useAgUiRuntime + HttpAgent Provider(v3.0 新增) |
| `src/web-ui/src/components/assistant-ui/tools.tsx` | `Tools` toolkit 工具渲染器(v3.0 新增) |
| `src/web-ui/src/components/assistant-ui/thread.tsx` | assistant-ui Thread 组件(registry 安装) |
| `src/web-ui/src/pages/OntologyWorkspace.tsx` | 表格/卡片/图谱三模式 + 批量创建 |
| `src/web-ui/src/components/OntologySidebar.tsx` | 侧栏搜索过滤 |
| `docs/ai-integration-guide.md` | 本文档 |

> **已删除(v2.0 遗留)**:`services/ai_assistant.py`、`api/client.ts` 中的 `streamAiJson()`/`parseAiJson()`、`POST /ai/stream` 端点。
