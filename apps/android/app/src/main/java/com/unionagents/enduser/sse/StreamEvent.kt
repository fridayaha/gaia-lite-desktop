package com.unionagents.enduser.sse

import kotlinx.serialization.json.JsonElement

sealed interface StreamEvent {
    data object Connected : StreamEvent

    data class ContentDelta(val text: String) : StreamEvent
    data class ReasoningDelta(val text: String) : StreamEvent

    data class HermesDelta(val delta: String) : StreamEvent
    data class HermesReasoning(val text: String) : StreamEvent

    data class RunStarted(val runId: String) : StreamEvent
    data class ToolStarted(
        val name: String,
        val preview: String,
        val toolCallId: String?,
    ) : StreamEvent
    data class ToolCompleted(val name: String, val error: String?, val result: String? = null) : StreamEvent

    data class ApprovalRequested(
        val runId: String,
        val command: String,
        val description: String,
        val choices: List<String>,
    ) : StreamEvent
    data class ApprovalResponded(val choice: String) : StreamEvent

    data class Completed(val usage: JsonElement? = null, val output: String? = null) : StreamEvent
    data class Failed(val error: String) : StreamEvent
    data object Cancelled : StreamEvent

    /** gateway 静默看门狗提示帧：引擎超过 N 秒未产出任何字节（多为长工具参数生成期）。 */
    data class SilenceHint(val elapsedSeconds: Int) : StreamEvent
}
