# ADR-010：本体工具层 HITL 分级审批（Sprint 2）

| 字段     | 内容 |
| -------- | ---- |
| **状态** | 已采纳（2026-06-24 实施修订：AG-UI 路径改为 pydantic-ai 原生 interrupt/resume） |
| **审批日期** | 2026-06-19 |
| **实施修订** | 2026-06-24：原设计的 `NEED_APPROVAL` marker + `ApprovalStore` + `/ai/action/confirm` + `AGUIApprovalHandler` AG-UI 闭环已被 pydantic-ai 2.0 原生 interrupt/resume 取代（见 `docs/bugfix/hitl-batch-approval-pending-pydantic-ai.md` §4–5、`docs/architecture/rfcs/hitl-batch-approval.md` v2/v3）。write/action 工具声明 `metadata={risk_level}`，`MetadataApprovalToolset` wrapper 触发 `ApprovalRequired`，AGUIAdapter 转 AG-UI interrupt，前端 `BatchApprovalPanel` 批量审批 + `resume` 续跑。MCP 路径不变（`MCPApprovalHandler` + `ctx.elicit` 同步审批）。下文描述的 AG-UI 闭环机制仅作历史背景，实际实现以 RFC v2 和 bugfix 文档为准。 |
| **影响层** | `tools/executor.py`（HITL 切面）+ `tools/toolsets/write.py`、`action.py`（写/执行工具）+ `tools/state.py`（AppState）+ `protocols/mcp_server.py`（MCP elicitation）+ `services/ai_agent.py`、`routes/ai.py`（AG-UI 闭环）+ `core/schemas/ontology.py`、`action.py`、`models/ontology.py`（risk_level 字段）+ 前端 `thread.tsx`、`api/client.ts`、`api/types.ts` |
| **相关文档** | [ontology-tool-layer.md](./ontology-tool-layer.md)（Sprint 2 章节）、[ADR-009](./adr-009-ontology-tool-layer.md)（Sprint 1 工具层基线）、[ai-integration-guide.md](../engineer/ai-integration-guide.md)（AG-UI 机制） |
| **前置** | ADR-009（只读工具层） |

---

## 背景

ADR-009 落地了 13 个只读工具 + MCP/AG-UI 双协议暴露。Sprint 2 要补"写/执行"能力——本体建模变更（define_object_type 等）和动作执行（invoke_action）。这些工具有副作用，按 CLAUDE.md「分级确认」红线和 reference.md §三动作族契约，必须有人工审批护栏。

reference.md §56 描述了 Palantir 的"草稿/建议生成"——高危动作生成草稿触发审批流。Gaia 的 HITL 需要在 MCP（外部 Agent）和 AG-UI（内置 Web UI）两条路径上都工作。

## 决策

### 1. 分级审批：risk_level 三级，low 跳过

| risk_level | 审批 | 触发 |
|-----------|------|------|
| `low`（默认） | **跳过**，直接执行 | 未显式标注的动作 |
| `medium` | 列影响范围确认 | 建模时显式标注；写类工具固定 medium |
| `high` | 输名称（AG-UI）/ 是-否（MCP） | 建模时标注高危（删除类） |

`low = 跳过`（而非"轻量审批"）：减少打扰，相信 LLM；medium/high 作为显式 opt-in 的强护栏。`ActionType.risk_level` 字段驱动动作族工具的 gating；写类工具固定 medium（建模变更都算中危）。

### 2. ActionType 加 risk_level 字段

`ActionType` schema/ORM 加 `risk_level: Literal["low","medium","high"]`，默认 `low`（已并入 Alembic 初始 revision，原 20260619 migration 已删除）。`ActionTypeCreate` 透传，建模时可标注。动作族工具运行时从元数据读 risk_level 驱动 gating——标注生效无需改代码。

### 3. ToolExecutor 扩 HITL 切面（治理收口）

`ToolExecutor.execute_gated()` 是写/执行工具的唯一入口：
- low → 跳过审批直接执行
- medium/high → 委托 `ApprovalHandler.request_approval()`
- 无 handler 挂载 → `NO_APPROVAL_HANDLER`（拒绝静默绕过 HITL）

`ApprovalStore` 按 thread_id 暂存 pending approval（多 pending/线程隔离），`confirm()` 恢复延迟执行。`confirm` 支持 action_id 全局兜底查找（action_id 是 uuid 全局唯一），让前端无需精确传 thread_id。Sprint 3 用 Redis 替换 ApprovalStore（同接口）。

### 4. HITL 双路径分叉（协议特定 handler）

`ApprovalHandler` 是 Protocol，两条路径各实现一个：

| 路径 | Handler | 机制 |
|------|---------|------|
| **AG-UI** | `AGUIApprovalHandler` | raise `NeedsApprovalError` → `execute_gated` 返回 `NEED_APPROVAL` marker → 前端弹窗 → `POST /ai/action/confirm` → `ToolExecutor.confirm` 恢复 |
| **MCP** | `MCPApprovalHandler` | `fastmcp.Context.elicit()` → 客户端原生弹窗（Claude Desktop）→ 同步返回 bool |

**MCP 不做降级**（per 决策 4）：不支持 elicitation 的客户端直接报错。高危在 MCP 下也走是-否（elicitation 无法强制输名称）；输名称是 AG-UI 独有的强确认。

AG-UI 路径不依赖 pydantic-ai 的 `requires_approval`（1.107.0 缺 #5441 自动闭环），走业务层中断——和 ADR-009 v3.0 集成指南定性一致，但这次 `confirm` 端点真正调 Service 执行（非 v3.0 前端二次 API）。

### 5. 共享逻辑 + 双套暴露（方案 A）

write/action 工具需要请求级 executor（带协议特定 handler），但 AG-UI（pydantic-ai `RunContext.deps`）和 MCP（`fastmcp.Context`）的请求上下文注入机制不同。解法：

- `<tool>_logic(executor, ...)` 协议无关函数（薄包装 Service + 构造影响文案 + 调 execute_gated）——单一事实源
- AG-UI 暴露：`@ts.tool` 从 `ctx.deps.executor` 拿 executor，转发到 `_logic`
- MCP 暴露：`@mcp.tool` 从 `fastmcp.Context` 构造 executor（绑 MCPApprovalHandler），调同一 `_logic`

工具逻辑写一次，两套暴露各几行胶水。`AppState`（AG-UI deps，持 thread_id + 请求级 executor）移到 `ontology.tools.state` 避免循环 import。

### 6. 影响范围工具内构造

每个写工具在自己的 `_logic` 里构造影响文案（"将创建对象类型 Order 含 2 属性，MANAGED 触发 Doris 建表..."）——工具最清楚自己的影响，Executor 只负责"拿影响 + 弹审批"，不生成影响文案。

## 替代方案

| 方案 | 否决理由 |
|------|---------|
| 统一走业务层中断（MCP 不用 elicitation） | MCP 客户端不会自动弹窗，外部 Agent 体验差 |
| MCP elicitation 做降级（不支持时返回 NEED_APPROVAL） | 决策 4 否决；不支持就报错，保证可用性清晰 |
| `risk_level` 默认 medium（未知风险倾斜安全） | 决策定 low，减少打扰相信 LLM；medium/high 显式 opt-in |
| 按 action_id 全局索引（不按 thread_id） | 线程隔离弱；thread_id 主索引 + action_id 全局兜底兼顾 |
| write/action 工具单套暴露（不分 AG-UI/MCP） | 请求级上下文注入机制不同，单套签名无法同时服务两边 |
| confirm 端点前端二次调 Service（v3.0 模式） | ADR-009 已批评；v4.1 confirm 真正调 ToolExecutor.confirm 执行 |
| 用 pydantic-ai `requires_approval` | 1.107.0 缺 #5441，AGUIAdapter 无自动闭环（集成指南 §2.3 已定性） |

## 后续工作

| 项 | 阶段 | 说明 |
|----|------|------|
| 高危输名称确认（AG-UI） | Sprint 3 | 当前高危只弹是/否；输名称强确认待前端补输入框 |
| Claude Desktop elicitation 实测 | Sprint 2 收尾 | 代码就绪，需真实 Claude Desktop 环境验证 elicit 弹窗 |
| ApprovalStore Redis 持久化 | Sprint 3 | 当前进程内 dict，重启丢失；Redis 替换同接口 |
| 治理 Principal + 权限 | Sprint 3 | 当前 principal=anonymous，HITL 不区分用户 |
| 审计入库 | Sprint 3 | 当前 audit 只写日志，未入专用库 |

## 参考

- [docs/architecture/ontology-tool-layer.md](./ontology-tool-layer.md) — Sprint 2 章节（完整能力架构交接文档）
- [docs/architecture/adr-009-ontology-tool-layer.md](./adr-009-ontology-tool-layer.md) — Sprint 1 工具层基线
- [docs/architecture/implementation-status.md](./implementation-status.md) — 实现状态 + 后续路标（§三-bis，含 HITL 待办）
- [docs/engineer/ai-integration-guide.md](../engineer/ai-integration-guide.md) §2.3 — HITL v3.0 定性（requires_approval 不可用）
- [CLAUDE.md](../../CLAUDE.md) — 分级确认红线（低危弹窗/中危列影响/高危输名称）+ 规范 8（联邦查询 SQL 不手写翻译器）
- [docs/reference.md](../reference.md) §56 — Palantir 草稿/审批机制参照
