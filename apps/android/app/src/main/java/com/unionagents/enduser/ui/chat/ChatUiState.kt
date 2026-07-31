package com.unionagents.enduser.ui.chat

import com.unionagents.enduser.sse.PendingRunStore
import com.unionagents.enduser.net.dto.Message
import com.unionagents.enduser.net.dto.ToolCallState
import com.unionagents.enduser.ui.chat.components.ActivityEvent

data class ApprovalState(
    val runId: String,
    val command: String,
    val description: String,
    val choices: List<String>,
    val submitting: Boolean = false,
    val responded: Boolean = false,
    val respondedChoice: String? = null,
)

/**
 * gateway.silence 提示帧是否应更新到"等待回复"事件上：仅当屏幕上没有任何其它活动指示时。
 * 工具执行中（tool.started 已到、tool.completed 未到）有工具卡、待审批有审批卡，
 * 此时改写"等待回复"既冗余又语义错误（是执行不是思考）；长工具参数生成期工具卡尚未
 * 出现，提示照常更新——这正是看门狗要覆盖的死寂场景。
 */
fun shouldShowSilenceHint(toolCalls: List<ToolCallState>, approvalPending: ApprovalState?): Boolean =
    toolCalls.none { !it.completed } && approvalPending == null

data class ChatUiState(
    val agentId: String? = null,
    val agentName: String? = null,
    val engineType: String? = null,
    val deployStatus: String? = null,
    val deployErrorMessage: String? = null,
    val engineAvailable: Boolean = true,
    val bootstrapping: Boolean = true,
    val sessions: List<com.unionagents.enduser.net.dto.Session> = emptyList(),
    val currentSessionId: String? = null,
    val messages: List<Message> = emptyList(),
    val loadingSessions: Boolean = false,
    val loadingMessages: Boolean = false,
    val models: List<String> = emptyList(),
    val currentModel: String? = null,
    val modelSheetOpen: Boolean = false,
    val drawerOpen: Boolean = false,
    val isStreaming: Boolean = false,
    val streamingContent: String = "",
    val thinkingText: String = "",
    val toolCalls: List<ToolCallState> = emptyList(),
    // 活动事件流（仅流式期间维护，落定消息不快照）：run 启动 / 模型 / 工具 / 审批 / 失败
    // 等过程的紧凑审计日志，供活动 feed 渲染；对齐 web `activityEvents`
    val activityEvents: List<ActivityEvent> = emptyList(),
    val approvalPending: ApprovalState? = null,
    // 当前 run 起始时间（epoch ms），RunStarted 时置位，Completed/Failed/Cancelled 时清零；
    // Completed 时用于计算 liveDurationSec 快照到消息上
    val turnStartedAt: Long? = null,
    val pendingRuns: List<PendingRunStore.PendingRun> = emptyList(),
    val feedback: Map<String, String> = emptyMap(), // key=message_ref（"mid:{id}"/"hash:{..}"）→ "up"/"down"（当前会话）
    val favorites: Set<String> = emptySet(), // 当前会话已收藏消息的 message_ref 集合
    val speakingRef: String? = null, // 正在 TTS 朗读的消息 message_ref；null=空闲
    val autoSpeak: Boolean = false, // 自动朗读开关：打开后每条 assistant 回复完成时自动 TTS 播放
    val error: String? = null,
    val retryable: Boolean = false,
    val toast: String? = null,
    val reconnecting: Boolean = false,
    val reconnectAttempt: Int = 0,
    val developerMode: Boolean = false,
    val multiSelectMode: Boolean = false,
    val selectedSessionIds: Set<String> = emptySet(),
)
