# RFC: HITL 审批机制重新设计（基于 AG-UI 原生 interrupt/resume）

> 状态：**已实施（2026-06-24，pydantic-ai 2.0）**
> 作者：架构 / 首席工程评审
> 日期：2026-06-23（v2，基于业界调研重写；v3 决策更新；2026-06-24 实施）
> 关联：ADR-010（HITL）、`AI-context-scoping.md`、`docs/engineer/ai-integration-guide.md`
> 前序：v1 提出"会话级 auto-grant"，已实施但验证发现设计缺陷，已回退；v2 提出基于 AG-UI 原生 interrupt/resume 的重新设计
>
> **v3 决策（2026-06-23）**：调研确认 pydantic-ai PR #5441（AGUIAdapter ↔ AG-UI interrupt 映射）已合并到 main（2026-06-13），下一个发布版本（1.108 / 2.0 正式版）将原生支持。决策选择 A：**等下一个 pydantic-ai 版本，不自补全 route 层**。v1 的 auto_grant 代码已全部删除，HITL 暂时回退到逐个 NEED_APPROVAL 确认。待 1.108+ 发布后，按 §4 实施 v2（工具改 `requires_approval=True`，前端批量审批面板）。
>
> **实施记录（2026-06-24）**：pydantic-ai 2.0 正式版发布（2026-06-23），含 PR #5441。§4 已实施：工具声明 `metadata={risk_level}`（注：不用 `requires_approval=True`——它会设 kind=unapproved 绕过 call_tool，导致 metadata 无法透传到 interrupt；改用 `MetadataApprovalToolset` wrapper 在 call_tool raise `ApprovalRequired(metadata=tool_def.metadata)`），前端 `BatchApprovalPanel` 监听 `unstable_getPendingInterrupts` + 提交 `unstable_submitInterruptResponses`。详见 `docs/bugfix/hitl-batch-approval-pending-pydantic-ai.md` §5。

---

## 0. TL;DR

v1 实施了"会话级 auto-grant"（用户点按钮 → 后续 medium 工具跳过审批），但实测暴露两个根本问题：
1. **时序错配**：grant 在 LLM 已收到 NEED_APPROVAL 之后才生效，对已中断的工具无效；LLM 不会自动续跑。
2. **LLM 幻觉**：LLM 收到 NEED_APPROVAL 后编造"28 个在审批队列"等不存在的话术。

**根因是违背业界共识**：自造 `NEED_APPROVAL` marker + `/ai/action/confirm` 轮子，没用 AG-UI 协议**原生**的 interrupt/resume 机制；且所有 medium 操作无差别走人工审批，无分层，导致 approval fatigue。

本 RFC 基于业界调研（AG-UI 协议、LangGraph、AI SDK、Anthropic Claude Code、VS Code Tool Confirmation Carousel）提出重新设计：**改用 AG-UI 原生 interrupt/resume，一轮的多个写工具中断聚合成批量审批面板，用户一次"全选批准"对应 resume 数组，agent 在新 run 自动续跑完成**。同时引入分层授权降低 approval fatigue。**未评审通过前不动代码。**

---

## 1. 业界调研结论

### 1.1 AG-UI 协议原生 interrupt/resume（我们正在用的协议）

AG-UI 协议**原生支持** HITL，且专为批量场景设计。核心机制（来源：`ag-ui/docs/concepts/interrupts.mdx`）：

**生命周期**：
```
Run 1: agent 执行 → 需要用户输入 → 发 StateSnapshot/MessagesSnapshot
       → RUN_FINISHED { outcome: { type: "interrupt", interrupts: [...] } }
Run 2: 客户端发 RunAgentInput { resume: [{interruptId, status, payload}] }
       → agent 继续 → ToolCallResult → RUN_FINISHED { outcome: { type: "success" } }
```

**并行 interrupts（批量审批的关键）**：一次 RunFinished 可带**多个** interrupt，客户端用一个 `resume` 数组**一次性回应全部**：
```json
// Run 1 结束：3 个工具调用待审批
{ "outcome": { "type": "interrupt", "interrupts": [
  { "id": "i-1", "reason": "tool_call", "toolCallId": "tc-a", "message": "..." },
  { "id": "i-2", "reason": "tool_call", "toolCallId": "tc-b", "message": "..." },
  { "id": "i-3", "reason": "tool_call", "toolCallId": "tc-c", "message": "..." }
]}}

// 客户端批量 resume：批准 2 个，取消 1 个
{ "resume": [
  { "interruptId": "i-1", "status": "resolved", "payload": { "approved": true } },
  { "interruptId": "i-2", "status": "resolved", "payload": { "approved": true } },
  { "interruptId": "i-3", "status": "cancelled" }
]}
```

**协议规则**：
- resume 必须覆盖**所有** open interrupts（不支持部分 resume）
- agent 在 resume 的 run 里**不重发** ToolCallStart/Args，只发 ToolCallResult（针对原 toolCallId）
- 同 threadId；幂等；可带 `responseSchema`（支持 approve-with-edits）
- `reason` 分类：`tool_call`（绑工具调用）、`input_required`、`confirmation`

**这是协议级别的批量审批标准**——Gaia 当前完全没用，自造了 `NEED_APPROVAL` marker + `/ai/action/confirm` 的轮子，丢失了批量能力。

### 1.2 LangGraph interrupt() 模式

LangGraph 用 `interrupt()` 暂停 graph，`Command(resume=...)` 恢复。多个 interrupt 用 interrupt ID 配对 resume 值。本质与 AG-UI interrupt/resume 同构——这是业界共识模式。

### 1.3 AI SDK needsApproval 模式

- 工具 `needsApproval: true` → 返回 `tool-approval-request` part → 客户端收集决策 → 加 `tool-approval-response` → 再次调用 model
- **动态审批**：`needsApproval: async (input) => condition`（如金额>1000 才审批）——分层授权的雏形
- 防重试：denied 后 system instruction "do not retry it"

### 1.4 分层权限栈（Anthropic / VS Code 共识）

业界**不推荐**所有操作无差别走人工审批。共识是分层（来源：agentpatterns.ai Tool Confirmation Carousel）：

| 层 | 作用 |
|----|------|
| Sandbox | 限制爆炸半径 |
| **Allowlist** | **预授权常规操作，自动放行** |
| Auto-mode | 分类器置信度高的自动放行 |
| Deferred/relay | 离线审批转发 |
| **Carousel/批量面板** | 残留的、真正需人工判断的，用轮播 UI 批量审 |

**关键洞见**：
- Anthropic 报告 Claude Code 通过 allowlist 预授权只读操作**减少 84% 提示**
- **"approval fatigue"是真风险**：用户无脑点批准，反而更不安全
- VS Code 1.116 的 Tool Confirmation Carousel：批量审阅，但**保留 per-call verdict**（批量审阅，不批量执行；无 blanket-approve）
- 会话级全局 grant 属于"blanket-approve"，**业界明确不推荐**（rubber-stamping）

---

## 2. v1 设计的缺陷复盘

### 2.1 已实施的 v1 机制
- `ApprovalStore.set_auto_grant(thread_id, risk_level)`：会话级全局开关
- `execute_gated` 前置 `check_auto_grant`：命中则跳过 NEED_APPROVAL
- 前端"批准并自动批准后续"按钮 + 指示条
- system prompt 教 LLM 识别"全部实施"

### 2.2 实测暴露的问题

**问题 1：时序错配（致命）**
LLM 调写工具 → 返回 NEED_APPROVAL → LLM 停下 → 用户点 grant → **grant 只对未来的工具生效，对已中断的无效** → LLM 不会自动续跑 → 卡死。

**问题 2：LLM 幻觉**
LLM 收到一堆 NEED_APPROVAL 后，编造"28 个在审批队列等待确认""请前往 Gaia 界面审批"等不存在的话术（系统无审批队列页，pending 实际为 0）。

**问题 3：违背业界共识**
会话级全局 grant = blanket-approve，业界明确不推荐（rubber-stamping，approval fatigue）。

**问题 4：自造轮子**
NEED_APPROVAL marker + `/ai/action/confirm` 没有批量能力，而 AG-UI 原生 interrupt/resume 专为批量设计。

### 2.3 根因
没有用协议原生能力，把"批量审批"当成"会话级全局放行"来解，方向错了。正确的解是"一轮的多个中断聚合，用户批量回应，agent 自动续跑"。

---

## 3. 重新设计：基于 AG-UI interrupt/resume + 分层授权

### 3.1 核心思路

**抛弃 NEED_APPROVAL marker + auto_grant，改用 AG-UI 原生 interrupt/resume**：
- agent 一轮里调多个写工具 → 每个工具调用对应一个 interrupt → RunFinished 带全部 interrupts
- 前端聚合成**批量审批面板**（一次看到 N 个待审，可逐个或全选批准/拒绝）
- 用户决策 → 前端发 `resume` 数组 → agent 在新 run 自动执行被批准的，返回 ToolCallResult，继续后续
- **天然支持"一次批准多个"**，且 agent 自动续跑，无时序错配

**叠加分层授权降低 approval fatigue**：
- low risk：自动放行（已有）
- medium risk：走 interrupt 批量审
- high risk：走 interrupt 逐个审（UI 强提示，不可全选）
- 可选 allowlist：特定工具/参数模式预授权（未来）

### 3.2 数据流（目标）

```
用户："建 10 个对象类型，全部实施"
Run 1:
  LLM 连续调 10 次 define_object_type
  每个 tool_call → agent 不立即执行，收集成 interrupts
  → RUN_FINISHED { outcome: { type: "interrupt", interrupts: [10 个] } }
前端:
  批量审批面板显示 10 个待审（"1/10 ... 10/10"）
  用户点"全部批准"（或逐个审）
  → POST /ai/agent { resume: [{interruptId, status:resolved, payload:{approved:true}}, ...10个] }
Run 2:
  agent 收到 resume → 对 approved 的执行 Service 调用 → 发 ToolCallResult
  → 继续后续（如有）→ RUN_FINISHED { outcome: { type: "success" } }
```

**关键差异**：用户一次"全部批准"对应 resume 数组，agent 自动续跑——无时序错配，无 LLM 幻觉（LLM 不需要理解审批状态，协议层处理）。

### 3.3 "全部实施"如何生效

**不再依赖 LLM 理解授权语义**。用户说"全部实施"→ LLM 正常调工具 → 协议把工具调用变成 interrupts → 前端面板显示 → 用户点"全部批准"。LLM 完全不参与审批逻辑，只管调工具。

这比 v1 的"LLM 判断意图"更可靠：审批是协议+UI 层的事，不污染 LLM 语义。

### 3.4 与 v1 的对比

| 维度 | v1（auto_grant） | v2（interrupt/resume） |
|------|------------------|------------------------|
| 批量审批 | 会话级全局放行（blanket） | 一轮 interrupts 聚合，批量 resume |
| 续跑 | grant 对已中断无效，卡死 | resume 自动启动新 run 续跑 |
| LLM 参与 | 需 LLM 理解授权（幻觉） | LLM 不参与审批逻辑 |
| 业界对齐 | 违背（blanket-approve） | 符合（AG-UI 原生 + 分层） |
| 协议 | 自造 marker | AG-UI 标准 |

---

## 4. 实施方案

### 4.1 后端

**4.1.1 改用 pydantic-ai 的 interrupt 机制**

pydantic-ai 配合 AGUIAdapter 应原生支持 interrupt（需核实版本）。write/action 工具不再返回 NEED_APPROVAL marker，改为触发 interrupt：
- 工具执行到需要审批时，raise interrupt（pydantic-ai 捕获，转成 AG-UI interrupt outcome）
- AGUIAdapter.dispatch_request 自动处理 resume 续跑

**核实项**：pydantic-ai 1.107 的 AGUIAdapter 是否支持 interrupt outcome + resume。若不支持，需在 route 层手动实现（解析 RunAgentInput.resume，调用 ToolExecutor 批量 confirm）。

**4.1.2 ToolExecutor 改造**

`execute_gated` 不再返回 NEED_APPROVAL marker，改为：
- 收集 tool_call → 暂存 deferred 执行
- run 结束时，若有待审 tool_calls，agent 发 interrupt outcome
- `/ai/agent` route 识别 `RunAgentInput.resume`：对每个 resume 项，approved 则执行暂存的 deferred 调用，cancelled 则跳过
- 批量执行后，agent 继续后续步骤

**4.1.3 移除 auto_grant**

删除 v1 的 `set_auto_grant`/`check_auto_grant`/`/ai/approval/grant`/`revoke`/`status`（会话级全局放行违背业界共识）。

**4.1.4 分层授权（保留+强化）**

- low risk：`execute_gated` 直接执行（已有）
- medium/high risk：走 interrupt
- high risk：interrupt 的 `metadata` 标注 `risk_level: high`，前端据此禁用"全选"、强提示

### 4.2 前端

**4.2.1 批量审批面板**

替换当前单个 `ApprovalDialog`：
- 监听 `RUN_FINISHED { outcome: { type: "interrupt" } }`
- 渲染批量面板：列出 N 个 interrupts（工具名 + impact + risk），"1/N"计数
- 操作：逐个批准/拒绝 + "全部批准"（high risk 不可全选）+ "全部拒绝"
- 决策后发 `resume` 数组（通过 `useAgUiRuntime` 的 resume API）

**4.2.2 移除 v1 的 grant UI**

删除"批准并自动批准后续"按钮、AutoGrantIndicator、AutoGrantContext、grant API 调用。

### 4.3 system prompt

- 删除 v1 加的"全部实施 = 已授权，连续执行"段落（不再需要 LLM 理解审批）
- 保留"收到 NEED_APPROVAL 不要编造话术"（防御性，虽然新机制不再返回 NEED_APPROVAL）
- 改为："写操作会自动进入批量审批，用户确认后自动执行；你只需正常调用工具，无需关心审批流程"

---

## 5. 风险与待确认项

- **R1**：pydantic-ai AGUIAdapter 对 interrupt/resume 的支持程度（核心未知）。**实施前先 spike 验证**：写最小用例确认 interrupt outcome 能否发出、resume 能否续跑。若 pydantic-ai 不支持，需在 route 层手动实现 resume 调度，工作量大。
- **R2**：assistant-ui `useAgUiRuntime` 对 interrupt 事件 + resume 提交的 UI 支持。需确认 `AgUiInterrupt`/`unstable_submitInterruptResponses` API 可用（调研见 types.d.ts 已导出）。
- **R3**：向后兼容。v1 的 auto_grant 代码删除后，已部署的前端（如有）会失效——但当前仅开发环境，无迁移负担。
- **R4**：high risk 不可全选的 UI 强制，需前端实现。

---

## 6. 实施计划（待批准后执行）

1. **Spike（0.5d）**：核实 R1/R2——pydantic-ai AGUIAdapter + assistant-ui useAgUiRuntime 的 interrupt/resume 支持。不通则评估手动实现成本，再决定。
2. **后端（1.5d）**：ToolExecutor 改 interrupt 模式；route 层 resume 调度；移除 auto_grant。
3. **前端（1d）**：批量审批面板；resume 提交；移除 v1 grant UI。
4. **测试（0.5d）**：单测 interrupt 收集/resume 续跑/high 不可全选；联调"建 10 个对象→批量批准→自动完成"。

---

## 7. 决策请求

1. **方向**：同意从 v1 auto_grant 切换到 v2 AG-UI 原生 interrupt/resume？
2. **R1 spike**：是否同意先做 pydantic-ai interrupt 支持的 spike，结果决定后续？
3. **分层**：medium 批量审 + high 逐个审（不可全选），是否认可？
4. **auto_grant 处置**：v1 代码直接删除（无生产部署），还是保留过渡？
