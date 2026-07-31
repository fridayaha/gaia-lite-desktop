# Ontology Modeling 端到端审视：架构优化建议

> **日期**: 2026-07-08
> **背景**: ontology-modeling skill 迁移为 pydantic-ai Capability（form A）后，端到端真实 LLM 测试暴露的架构问题与优化方向。
> **关联**: [ontology-modeling-spec.md](./ontology-modeling-spec.md)（skill 迁移设计）、[ai-integration-guide.md](../engineer/ai-integration-guide.md)

---

## 一、端到端测试暴露的问题（按严重度排序）

### P0 — HITL 审批展示的是"原始参数"而非"真实效果"

**现象**：LLM 调 `define_object_type(ontology="", ...)`，AG-UI interrupt message 展示给用户的是 `ontology=""`（LLM 原始参数），但执行时会回退成 `Marketing`。用户看到空 ontology 会困惑"这是要建到哪个本体？"，无法从预览预测真实结果。

**架构根因**：违反 HITL 的"propose → review → commit"原则。业界最佳实践明确："**show the effect of the action, not the JSON**"、"**the preview must reflect the real effect computed by your code — never the model's claim about itself**"（[AI/TLDR HITL UX](https://ai-tldr.dev/learn/building-ai-apps/ai-ux-patterns/human-in-the-loop-ux/)）。当前 `MetadataApprovalToolset` 把 LLM 的原始 tool call args 直接塞进 interrupt message，没有经过"效果计算"层。

**影响**：用户无法准确审批——这是 HITL 的核心失败模式（"confirmation fatigue"的前置：用户看不懂预览，就开始无脑点 approve）。

### P1 — 写工具的"参数默认值"与读工具不一致（已修复）

**现象**：read-only 工具（`list_object_types` 等）有 `ontology = ontology or ctx.deps.ontology` 回退；write/action 工具（`define_object_type` 等）没有。LLM 传 `ontology=""` 时，读工具能正常工作，写工具审批后 NotFoundError 失败。

**架构根因**：工具的"参数解析契约"不统一。同一个 `ontology` 参数，在不同工具集里有不同的默认行为，这是**契约不一致**的设计缺陷——LLM 难以建立稳定的心智模型（"这个参数到底要不要传？"）。

**影响**："approve then fail" 反模式——用户审批后执行失败，严重损害信任。**已在本轮修复**（6 个写工具加回退 + 7 个测试）。

### P2 — Capability 的"按需加载"依赖 LLM 自主判断，简单场景不加载

**现象**：复杂建模场景（采购系统）LLM 主动调 `load_capability`；简单建模场景（单对象）LLM 不加载，凭自身能力直接调 `define_object_type`。

**架构根因**：这是 progressive disclosure 的固有特性——"**Agents rely on their own judgment to select the relevant skill. Ambiguous task descriptions or poorly-named skills cause the agent to load the wrong skill**"（[AgentPatterns.ai](https://agentpatterns.ai/agent-design/progressive-disclosure-agents/)）。`buildOntologyQueryPrompt` 已经告诉 LLM "write tools are available... just call the tools"，与 capability 的"加载我才能更好建模"形成**职责竞争**，LLM 选了前者。

**影响**：简单场景下方法论里的 M:N 拆分、数据类型红线等规则没生效，全靠 LLM 自觉。短期可接受（LLM 自身能力够），长期是质量隐患（LLM 换模型/降级时建模质量不可控）。

### P3 — 工具规模逼近"tool selection 退化"阈值

**现象**：当前 Agent 挂载 **18 个 function tools**（含 `load_capability`）。

**架构根因**：业界研究一致表明"**An agent with 80 tool definitions performs measurably worse on tool selection than one with 6**"（[FIM One](https://docs.fim.ai/architecture/progressive-disclosure)）。虽然 18 远未到 80，但已过"6"的安全线。更关键的是，这 18 个工具横跨 4 个不同关注点（查询/画布/建模/动作），LLM 在"查询"场景要忽略 8 个写工具，在"建模"场景要忽略画布工具——**attention dilution**。

**影响**：tool selection 准确率随工具数下降；未来加工具会加剧。

---

## 二、优化方案（按优先级，含业界最佳实践依据）

### 优化 1（P0）：HITL 审批展示“计算后的真实效果”，而非原始参数 ✅ 已实施

**目标**：interrupt message 展示用户能看懂的“将创建 Coupon 对象到 Marketing 本体，含 4 个属性...”，而非 `define_object_type(ontology="", ...)`。

**业界依据**：
- "Show **what will happen** in plain language, not raw JSON tool payloads"（[LucaNerlich HITL](https://github.com/LucaNerlich/lucanerlich.com/blob/main/docs/ai/human-in-the-loop.md)）
- "The preview must reflect the real effect computed by your code — never the model's claim about itself"（[AI/TLDR](https://ai-tldr.dev/learn/building-ai-apps/ai-ux-patterns/human-in-the-loop-ux/)）
- "propose → review → commit：AI 产生 proposed action，app 渲染成人类可读预览，用户 review/edit 后才 commit"（同上）
- AG-UI 协议规范：`message` 是 "Human-readable prompt. Universal fallback UI content"，`metadata` 是 "Free-form framework-specific data"（[AG-UI Interrupts](https://docs.ag-ui.com/concepts/interrupts)）——协议设计就是让前端优先用 message，metadata 补充

**实施**（2026-07-08）：
- 新增 `src/ontology/tools/toolsets/impact_builder.py`——按工具名注册纯函数 impact builder，生成 `impact_summary`（人类可读效果描述）+ `resolved_args`（应用默认值后的参数，如 `ontology="Marketing"`）。builder 是纯函数，无 I/O，只在审批前运行。
- 改造 `MetadataApprovalToolset.call_tool`——raise `ApprovalRequired` 前调 `build_impact`，把 `impact_summary` + `resolved_args` 合并进 `metadata`（保留原有 `risk_level`）。
- 前端 `BatchApprovalPanel.parseInterruptDisplay` 优先读 `metadata.impact_summary` 渲染纯文本预览，fallback 到 message 解析的 raw JSON。

**架构意义**：把"LLM 意图"和"执行效果"在 HITL 边界显式分离。LLM 产出意图（可能含空 ontology），代码计算效果（填入 Marketing + 生成预览文案），用户审批效果。这是 HITL 的标准 propose→review→commit 分层。

**端到端验证**（真实 LLM）：`define_object_type(ontology="")` 的 interrupt 现在 metadata 里携带：
- `impact_summary`: `将在本体 Marketing 创建对象类型 优惠券 (Coupon),主键 (由属性标记推导),storage_type=MANAGED,含 4 个属性。MANAGED 类型会触发 Doris 建表 + 索引同步。`
- `resolved_args.ontology`: `Marketing`（不再是空字符串）
- `message`（raw）仍保留原始 JSON 作为 fallback

**测试**：`tests/unit/tools/test_impact_preview.py`（11 用例）—— impact builder 纯函数（6 个工具各 1 个 + 边界）+ MetadataApprovalToolset 集成（enrichment + read-only pass-through）。

### 优化 2（P2）：把建模方法论从"按需 capability"改为"条件注入 instructions"

**目标**：建模类对话始终有方法论指导，不依赖 LLM 自主加载；查询类对话不被污染。

**业界依据**：
- Progressive disclosure 的失败模式之一："Wrong skill loaded: Agents rely on their own judgment"（[AgentPatterns.ai](https://agentpatterns.ai/agent-design/progressive-disclosure-agents/)）
- FIM One 的模式选择："`SKILL_TOOL_MODE=inline` embeds the full Skill content directly in the system prompt. Suitable when you have few, small Skills"（[FIM One](https://docs.fim.ai/architecture/progressive-disclosure)）——当 skill 少且小时，inline 比 progressive 更可靠
- "An agent with a single small Skill might use inline mode"（同上）——我们只有一个建模 skill

**方案**：把 `OntologyModelingCapability` 从 `defer_loading=True` 改为**条件 instructions**——用 `@agent.instructions` 或 `Capability(get_instructions=callable)`，callable 读 `ctx.deps` 或对话意图判断是否是建模场景，是则注入方法论，否则返回 None。这比 deferred 更可靠（不依赖 LLM 主动加载），比 always-on 更精准（查询轮不注入）。

**权衡**：方法论约 1500 tokens，inline 模式下每个建模轮多花这些 token。但建模是低频高价值操作，token 成本可接受；且避免了“LLM 忘记加载导致方法论失效”的质量风险。查询轮 callable 返回 None，零开销。

**实施验证**（2026-07-08）：Agent 级 `instructions=[callable]` 机制可行——callable 接收 `RunContext[AppState]`，per-run 返回 `str | None`，返回 None 时不注入（零开销），返回 str 时注入。现有 `_current_date_instruction` / `_canvas_state_instruction` 已是此模式。难点在于“如何判断建模场景”：`ctx.deps` 无建模标志，从消息内容关键词判断太脆弱。**更可靠的变体**：拆成“核心红线 always-on（~300 tokens，数据类型/M:N/ActionType 契约，每轮都在）+ 完整方法论 deferred（细节按需加载）”——核心红线保证关键规则始终生效，完整方法论补充场景化指导。

**备选方案**（更轻量）：保持 deferred，但在 `buildOntologyQueryPrompt` 里加一句"建模类问题先调 `load_capability('ontology-modeling')`"——把"是否加载"从 LLM 自由判断改为 prompt 明确指令。成本最低，但可靠性低于 inline。

### 优化 3（P3）：按"关注点"拆分 Agent 或用 tool 分组，缓解 attention dilution

**目标**：查询场景只暴露查询工具，建模场景只暴露建模工具。

**业界依据**：
- "LLMs pay for context in two currencies: tokens and attention. Every tool definition injected costs both"（[FIM One](https://docs.fim.ai/architecture/progressive-disclosure)）
- "An agent with 80 tool definitions performs measurably worse on tool selection than one with 6"
- pydantic-ai 原生支持 `FilteredToolset` / `PreparedToolset` / `prepare_tools` hook（[pydantic-ai toolsets](https://pydantic.dev/docs/ai/tools-toolsets/toolsets/)）——可按 run context 动态过滤工具

**方案**（渐进式，三档）：
1. **轻量**：用 `prepare_tools` hook，根据 `ctx.deps` 或首条消息意图判断场景，过滤掉无关工具集（查询场景隐藏 write/action/canvas，建模场景隐藏 canvas/reasoning）。零架构改动。
2. **中量**：拆成两个 Agent——`QueryAgent`（只读工具 + 查询 prompt）和 `ModelingAgent`（全工具 + 建模方法论），前端按入口路由。清晰但增加前端复杂度。
3. **重量**：多 Agent 架构（Orchestrator + 专业子 Agent），参考 [AgentPatterns multi-agent](https://agentpatterns.ai/)。过度设计，当前规模不需要。

**建议**：先做轻量方案（`prepare_tools`），观察 tool selection 准确率，再决定是否升级。

### 优化 4（P1 巩固）：统一工具的"参数解析契约"

**目标**：所有工具的 `ontology` 参数行为一致——可省略，回退 `ctx.deps.ontology`。

**现状**：本轮已修复 6 个写工具，但这是"打补丁"。根因是工具参数契约没有统一规范。

**方案**：在 `tools/toolsets/` 建立约定——凡引用 `ontology` 的工具，参数签名用 `ontology: str = ""`，工具体首行 `ontology = ontology or ctx.deps.ontology`。写入 `docs/architecture/ontology-tool-layer.md` 作为工具开发规范。未来新工具遵循此约定，避免再次不一致。

---

## 三、优先级与实施建议

| 优化 | 优先级 | 工作量 | 建议时机 |
|------|--------|--------|----------|
| 1. HITL 展示真实效果 | P0 | 中（改 MetadataApprovalToolset + 前端渲染） | 近期——直接影响用户信任 |
| 2. 方法论条件注入 | P2 | 小（改 capability 配置） | 近期——低成本高可靠性 |
| 4. 参数契约统一 | P1 巩固 | 小（文档 + 约定） | 随优化 2 一起 |
| 3. tool 分组/拆 Agent | P3 | 中-大 | 观察后再定——先 `prepare_tools` 轻量方案 |

**核心原则**：HITL 是信任的边界（优化 1 最重要）；方法论要可靠生效而非依赖 LLM 自觉（优化 2）；工具规模要主动治理而非等退化（优化 3 监控）。

---

## 四、参考资料

- [Human-in-the-Loop UX: Designing AI Approvals](https://ai-tldr.dev/learn/building-ai-apps/ai-ux-patterns/human-in-the-loop-ux/) — propose→review→commit，"show effect not JSON"
- [LucaNerlich HITL](https://github.com/LucaNerlich/lucanerlich.com/blob/main/docs/ai/human-in-the-loop.md) — 审批 UX 原则
- [Progressive Disclosure - FIM One](https://docs.fim.ai/architecture/progressive-disclosure) — token 经济学，inline vs progressive 模式选择
- [Progressive Disclosure for Layered Agent Definitions](https://agentpatterns.ai/agent-design/progressive-disclosure-agents/) — skill 加载失败模式
- [Agent Skills: Progressive Disclosure as a System Design Pattern](https://www.newsletter.swirlai.com/p/agent-skills-progressive-disclosure) — 三层加载模型
- [pydantic-ai Toolsets](https://pydantic.dev/docs/ai/tools-toolsets/toolsets/) — FilteredToolset/PreparedToolset 原生扩展点
- [pydantic-ai Capabilities](https://pydantic.dev/docs/ai/core-concepts/capabilities/) — Capability/instructions 机制
