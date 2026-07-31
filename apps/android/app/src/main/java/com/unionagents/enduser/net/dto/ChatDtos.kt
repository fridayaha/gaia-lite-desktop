package com.unionagents.enduser.net.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.Transient
import com.unionagents.enduser.ui.chat.components.ActivityEvent

@Serializable
data class Session(
    @SerialName("session_id") val sessionId: String? = null,
    val id: String? = null,
    val title: String? = null,
    @SerialName("created_at") val createdAt: Double? = null,
    @SerialName("started_at") val startedAt: Double? = null,
    @SerialName("updated_at") val updatedAt: Double? = null,
    @SerialName("last_message_at") val lastMessageAt: Double? = null,
    val model: String? = null,
    val preview: String? = null,
) {
    val stableId: String get() = sessionId ?: id ?: ""
    val stableTitle: String get() = title?.ifBlank { null } ?: preview?.ifBlank { null } ?: "未命名"
    val stableCreatedAt: Double get() = createdAt ?: startedAt ?: 0.0
    val stableLastAt: Double? get() = lastMessageAt ?: updatedAt ?: startedAt
}

@Serializable
data class CreateSessionRequest(
    val model: String? = null,
)

@Serializable
data class CreateSessionResponse(
    val session: Session? = null,
)

@Serializable
data class UpdateTitleRequest(
    val title: String,
)

@Serializable
data class SessionListResponse(
    val sessions: List<Session>? = null,
    val data: List<Session>? = null,
)

@Serializable
data class Message(
    val id: Long? = null,
    val role: String,
    val content: String? = null,
    @SerialName("tool_calls") val toolCalls: List<ToolCall> = emptyList(),
    @SerialName("provider_details") val providerDetails: String? = null,
    val name: String? = null,
    val attachments: List<Attachment> = emptyList(),
    // 客户端侧元数据：本次回复对应的引擎 run_id（反馈上报/Langfuse 镜像用）。
    // 历史消息响应无此字段，仅流式完成时回填；不参与序列化。
    @kotlinx.serialization.Transient
    val runId: String? = null,
    // 客户端侧：本次回复的中间过程快照（思考 + 工具调用），渲染顺序对齐 hermes TUI
    // （先思考/工具、后回复）。历史消息无此数据，仅流式完成时从 UI state 快照；不参与序列化。
    @kotlinx.serialization.Transient
    val liveThinking: String? = null,
    @kotlinx.serialization.Transient
    val liveToolCalls: List<ToolCallState> = emptyList(),
    // 客户端侧：本次回复的活动事件快照（run 启动/已连接/Tool finished 等），
    // 渲染在回复气泡上方的中间过程收起栏内。历史消息无此数据，仅流式完成时从 UI state 快照；不参与序列化。
    @kotlinx.serialization.Transient
    val liveActivityEvents: List<ActivityEvent> = emptyList(),
    // 客户端侧：本次回复的用量元数据（model / 耗时 / tokens），完成时从 UI state 快照
    // 渲染在回复气泡底部对齐 web StatusCard；历史消息无此数据，不参与序列化。
    @kotlinx.serialization.Transient
    val liveModel: String? = null,
    @kotlinx.serialization.Transient
    val liveDurationSec: Double? = null,
    @kotlinx.serialization.Transient
    val liveTokens: Int? = null,
) {
    val isVisible: Boolean
        get() = role != "tool" && !(role == "assistant" && content.isNullOrBlank() && toolCalls.isNotEmpty())
}

/** 一次工具调用的 UI 快照（流式 ToolStarted/ToolCompleted 增量更新，Completed 时随消息落定）。 */
data class ToolCallState(
    val name: String,
    val preview: String,
    val toolCallId: String?,
    val completed: Boolean,
    val error: String?,
    val result: String? = null,
)

@Serializable
data class ToolCall(
    val id: String? = null,
    val type: String? = null,
    val function: ToolFunction? = null,
)

@Serializable
data class ToolFunction(
    val name: String? = null,
    val arguments: String? = null,
)

@Serializable
data class MessageListResponse(
    val messages: List<Message>? = null,
    val data: List<Message>? = null,
)

@Serializable
data class StartRunRequest(
    val session_id: String,
    val input: String,
    val model: String? = null,
    /**
     * Hermes 单 run 无状态：必须把先前 user/assistant 消息显式塞进请求体，
     * 否则引擎只看到本轮 input，"上面那个" 之类指代会因无上下文而答非所问
     * （引擎 session_search 工具检索不到上一轮结果 → 回 "这个 session 里还没提到过…"）。
     * 对齐 apps/enduser/src/composables/useChat.ts 的 sendHermesRun conversation_history 字段。
     * 注意：不含本轮刚追加的 user 消息（本轮 input 由 [input] 字段单独传）。
     */
    @SerialName("conversation_history") val conversationHistory: List<HistoryItem> = emptyList(),
    val user: String? = null,
    @SerialName("attachments") val attachments: List<Attachment> = emptyList(),
)

@Serializable
data class HistoryItem(
    val role: String,
    val content: String,
)

@Serializable
data class StartRunResponse(
    @SerialName("run_id") val runId: String,
)

@Serializable
data class RunStatusResponse(
    @SerialName("run_id") val runId: String? = null,
    val status: String? = null, // running / queued / waiting_for_approval / completed / failed / cancelled
    val error: String? = null,
)

@Serializable
data class ApprovalRequest(
    val choice: String, // once / session / always / deny
)

@Serializable
data class ApprovalResponse(
    val run_id: String? = null,
    val status: String? = null,
    val choice: String? = null,
)

@Serializable
data class UploadFileResponse(
    val filename: String? = null,
    val path: String? = null,
    val size: Long? = null,
    val mime: String? = null,
    @SerialName("is_image") val isImage: Boolean = false,
)

@Serializable
data class Attachment(
    val name: String,
    val path: String,
    @SerialName("is_image") val isImage: Boolean = false,
    val mime: String? = null,
    val size: Long? = null,
    @SerialName("thumbnail_url") val thumbnailUrl: String? = null,
    /** 本地 content Uri，仅用于聊天框/Composer 内即时预览；不参与 gateway 序列化。 */
    @Transient val localUri: String? = null,
)
