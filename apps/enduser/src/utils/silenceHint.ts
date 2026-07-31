/**
 * gateway.silence 静默提示是否应对用户可见。
 *
 * 静默帧由 gateway 注入（`services/gateway/app/proxy.py:_silence_hint_frame`）：
 * 转发引擎 SSE 时若超过 `sse_silence_hint_seconds`（默认 8s）无任何字节产出，
 * 网关下发 `{"event":"gateway.silence","elapsed":N}` 帧，提示用户"还在等"。
 *
 * 抑制规则：工具仍在运行 / 审批待响应时不显示——此时用户已有可见进度
 * （工具卡 / 审批卡），额外的"已等待 N 秒"会与之重复。仅在 run 跑过且
 * 无任何可见活动（pending dots 阶段 / 思考停滞）时显示。
 */
export function shouldShowSilenceHint(
  elapsed: number | null | undefined,
  toolCalls: Array<{ status?: string }>,
  approvalPending: unknown,
): boolean {
  if (elapsed == null || elapsed <= 0) return false
  if (approvalPending != null) return false
  const hasRunningTool = (toolCalls || []).some(
    (t) => (t?.status as string | undefined) === "running",
  )
  return !hasRunningTool
}
