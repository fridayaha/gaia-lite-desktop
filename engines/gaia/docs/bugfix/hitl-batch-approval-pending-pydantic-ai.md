# HITL 批量审批 — 等 pydantic-ai 原生 interrupt 支持

> 跟踪状态：**✅ 前置依赖已满足，进入实施（2026-06-24）**  
> 创建：2026-06-23  
> 关联：`docs/architecture/rfcs/hitl-batch-approval.md`（v2/v3）、ADR-010  
> 每日检查：见 §3 检查清单

## 1. 背景

AI 本体助手执行写/动作操作时，medium/high 风险工具逐个返回 `NEED_APPROVAL` marker 中断，用户被反复审批淹没；用户说"全部实施"无法穿透到执行层。

v1 自造了"会话级 auto-grant"机制，实测暴露两个致命问题（时序错配 + LLM 幻觉），且违背业界共识（blanket-approve → approval fatigue）。**v1 已全部回退删除**。

v2 方案：改用 AG-UI 原生 interrupt/resume + pydantic-ai `requires_approval` —— 一轮多个写工具中断聚合成批量审批面板，用户一次"全选批准"对应 `resume` 数组，agent 自动续跑。**前置要求**：pydantic-ai 的 `AGUIAdapter` 支持 `DeferredToolRequests` ↔ AG-UI interrupt 双向映射。

## 2. 前置要求（阻塞项）

pydantic-ai **PR #5441** "Map AG-UI interrupts ↔ DeferredTools in AGUIAdapter" 提供了所需支持：

| 项 | 状态 |
|----|------|
| pydantic-ai 内核 `requires_approval` → `DeferredToolRequests` 输出 | ✅ 1.107.0 已可用（spike 验证） |
| pydantic-ai `deferred_tool_results` 续跑 | ✅ 1.107.0 已可用（spike 验证） |
| `AGUIAdapter` outbound：`DeferredToolRequests` → `RUN_FINISHED { outcome: { type: "interrupt" } }` | ✅ 2.0.0 已补全（PR #5441） |
| `AGUIAdapter` inbound：`RunAgentInput.resume` → `DeferredToolResults` | ✅ 2.0.0 已补全（PR #5441，`deferred_tool_results()` 实测可见） |
| PR #5441 合并状态 | ✅ 已合并到 main（2026-06-13，commit `b77df7b`） |
| 依赖 `ag-ui-protocol 0.1.19` | ✅ 已在 PyPI |
| **pydantic-ai 发布版本含 PR #5441** | ✅ **2.0.0 正式版**（2026-06-23 发布，晚于合并 10 天） |

**结论**：前置要求已满足。pydantic-ai 2.0.0 正式版（非 prerelease，2026-06-23 发布）含 PR #5441，`HAS_INTERRUPT_SUPPORT: True` 实测通过（`AGUIAdapter.deferred_tool_results()` 把 `RunAgentInput.resume[]` 映射为 `DeferredToolResults`，outbound 走 `HAS_INTERRUPTS` / `RunFinishedInterruptOutcome` 路径）。进入 §4 实施。

## 3. 每日检查清单

每天执行以下检查，任一满足即触发 §4 实施：

### 检查命令

```bash
# 1. 查 pydantic-ai 最新版本（是否 > 1.107.0）
curl -s https://pypi.org/pypi/pydantic-ai/json | python3 -c "import sys,json; d=json.load(sys.stdin); print('latest:', d['info']['version'])"

# 2. 直接探测目标版本是否含 PR #5441 的标志符号
#    （AGUIAdapter 出现 RunFinishedInterruptOutcome / HAS_INTERRUPTS / _interrupt 模块）
TARGET_VER=<上一步看到的最新版本>
uv pip install --quiet "pydantic-ai-slim[ag-ui]==${TARGET_VER}" 2>/dev/null
uv run python -c "
import inspect
from pydantic_ai.ui.ag_ui import AGUIAdapter
src = inspect.getsource(AGUIAdapter)
print('HAS_INTERRUPT_SUPPORT:', 'RunFinishedInterruptOutcome' in src or 'HAS_INTERRUPTS' in src or 'interrupt' in src.lower())
"

# 3. 或查 main 分支已发布（看 GitHub releases 是否有 > v1.107.0 的 stable tag）
curl -s 'https://api.github.com/repos/pydantic/pydantic-ai/releases?per_page=5' | python3 -c "
import sys,json
for r in json.load(sys.stdin):
    if not r['prerelease']:
        print(r['tag_name'], r['published_at'])
"
```

### 触发条件（任一满足）

- [ ] pydantic-ai stable 版本 > 1.107.0 已发布，且 `HAS_INTERRUPT_SUPPORT: True`
- [ ] pydantic-ai 2.0 正式版（非 beta）发布，且 `HAS_INTERRUPT_SUPPORT: True`

### 检查记录

| 日期 | 最新 stable 版本 | 含 PR#5441？ | 备注 |
|------|------------------|--------------|------|
| 2026-06-23 | 1.107.0 | 否 | 1.107 发布 6-10，PR#5441 合并 6-13，下一版本待发 |
| 2026-06-24 | **2.0.0** | **是**（`HAS_INTERRUPT_SUPPORT: True`） | 2.0.0 正式版 6-23 发布；触发 §4 实施 |

## 4. 触发后的实施计划

前置要求满足后，按 `docs/architecture/rfcs/hitl-batch-approval.md` v2 §4 实施：

### 后端
1. write/action 工具：去掉自造的 `execute_gated` + `NEED_APPROVAL` marker，改用 pydantic-ai `requires_approval=True`（medium/high 风险工具标记）
2. `routes/ai.py`：`dispatch_request` 直接用原生 AGUIAdapter（无需自补全 route 层）；移除 `/ai/action/confirm`（pydantic-ai 接管 resume）
3. `ToolExecutor.execute_gated` 简化（不再需要 NEED_APPROVAL marker / ApprovalStore deferred 逻辑）
4. high risk：通过工具 `metadata` 标注 `risk_level: high`，前端据此禁用全选

### 前端
1. 删除单个 `ApprovalDialog`（NEED_APPROVAL marker 的 UI）
2. 新增批量审批面板：监听 `RUN_FINISHED { outcome: { type: "interrupt" } }` → 渲染 N 个待审（工具名 + impact + risk，"1/N"计数）→ "全部批准/全部拒绝/逐个" → 调 `useAgUiRuntime` 的 `submitInterruptResponses`
2. high risk 不可全选（metadata 标注 risk_level）

### system prompt
- 简化：删除 NEED_APPROVAL 相关说明（不再返回该 marker）
- 改为："写操作会自动进入批量审批，用户确认后自动执行；你只需正常调用工具，无需关心审批流程"

### 验证
- 单测：工具 `requires_approval` → `DeferredToolRequests` 输出；resume 续跑；high 不可全选
- 联调："建 10 个对象类型，全部实施" → 批量批准 → 自动完成无中断

## 5. 实施完成状态（2026-06-24）

§4 已实施完毕。临时状态已结束，medium 风险重新进入批量审批：

- 后端：write/action 工具声明 `metadata={"risk_level": ...}`（不声明 `requires_approval=True`），由 `MetadataApprovalToolset` wrapper 在 `call_tool` 阶段 `raise ApprovalRequired(metadata=tool_def.metadata)` 触发 defer。pydantic-ai 把 pending approvals 收集进 `DeferredToolRequests`，`AGUIAdapter` 转 `RUN_FINISHED { outcome: { type: "interrupt" } }`，前端批量面板 `resume` 后 `AGUIAdapter.deferred_tool_results` 映射回 `DeferredToolResults`，agent 续跑执行被批准的工具。
- **关键实现细节**：pydantic-ai 2.0 的 `requires_approval=True` 会设 `ToolDefinition.kind='unapproved'`，导致 pydantic-ai **直接收集 defer 而绕过 `call_tool`**，工具静态 `metadata=` 无法流入 `DeferredToolRequests.metadata`。因此改用 `MetadataApprovalToolset` wrapper（不声明 requires_approval，靠 wrapper 在 call_tool raise ApprovalRequired 携带 metadata），确保 `risk_level` 透传到 AG-UI `Interrupt.metadata`，前端据此决定 per-item vs blanket-approve。
- write 工具（define_object_type 等，全 medium）→ 批量审批面板，可全选
- action 工具（invoke_action，risk_level 运行时从 ActionType 读）→ 静态 `risk_level="unknown"`，前端默认逐个审（不可全选，保守）
- 前端 `ApprovalDialog`（单个 NEED_APPROVAL 确认）已删除，替换为 `BatchApprovalPanel`（监听 `unstable_getPendingInterrupts`，提交 `unstable_submitInterruptResponses`）
- `/ai/action/confirm` 端点、`AGUIApprovalHandler`、`ApprovalStore`、`NeedsApprovalError`、`ToolExecutor.confirm` 全部删除
- MCP 路径不变：`MCPApprovalHandler` 仍用 `ctx.elicit` 同步审批，走 `execute_gated`（MCP 不用 interrupt/resume）
- read-only 助手主流程不受影响

### 已删除的临时机制
- `executor.py` 的 `risk_level != "high"` 临时放宽逻辑
- `AGUIApprovalHandler`（raise NeedsApprovalError）
- `ApprovalStore` / `ApprovalRequest` 的 thread_id/execute/action_id 字段（ApprovalRequest 简化为 MCP 用的数据载体）
- `ToolExecutor.confirm` 方法
- 前端 `confirmAiAction` / `NeedApprovalMarker` / `ActionConfirmResult` 类型

### 验证
- 后端单测：`tests/unit/tools/test_executor_hitl.py`（audit + execute_gated MCP + execute_write 分流）、`tests/unit/tools/test_write_logic.py`、`tests/unit/ai/test_agui_interrupt.py`（interrupt/resume 全链路 spike：defer → metadata 透传 → resume → 工具体执行 / ToolDenied 跳过 / build_results(approve_all)）
- 全量后端测试 722 passed，ruff + mypy 干净，前端 tsc --noEmit 通过

## 6. 参考

- RFC：`docs/architecture/rfcs/hitl-batch-approval.md`
- pydantic-ai PR #5441：https://github.com/pydantic/pydantic-ai/pull/5441
- AG-UI interrupt 协议：https://docs.ag-ui.com/concepts/interrupts
- pydantic-ai Deferred Tools 文档：https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/
