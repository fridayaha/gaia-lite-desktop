package com.unionagents.enduser.sse

import com.unionagents.enduser.net.GatewayApi
import com.unionagents.enduser.net.ServerConfig
import com.unionagents.enduser.net.dto.Attachment
import com.unionagents.enduser.net.dto.HistoryItem
import com.unionagents.enduser.net.dto.StartRunRequest
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 双流编排 —— 镜像 apps/enduser/src/composables/useChat.ts 的 sendMessage / sendHermesRun / consumeHermesRunStream。
 *
 * - HERMES：POST /v1/runs 拿 run_id → GET /v1/runs/{id}/events SSE
 * - 非 HERMES：直接 POST /v1/chat/completions SSE 流式读
 *
 * 两条流都通过 [SseClient] 获取原始 payload flow，再路由到 [StreamEvent] sealed class。
 */
@Singleton
class ChatStreamRunner @Inject constructor(
    private val sseClient: SseClient,
    private val gatewayApi: GatewayApi,
    private val pendingRunStore: PendingRunStore,
    private val json: Json,
    private val serverConfig: ServerConfig,
) {
    /**
     * 非HERMES 流式发送。
     * @param agentId X-Agent-ID 头值（实际由 AgentHeaderInterceptor 注入，这里仅占位）
     * @param model 模型 id
     * @param messages 已有对话历史（OpenAI 格式 [{role, content}]）
     * @param attachments 本轮 user 消息附件（追加到最后一条 user message 上，对齐 web toEngineMessage）
     */
    fun streamChatCompletions(
        agentId: String,
        sessionId: String?,
        engineType: String?,
        model: String,
        messages: List<Pair<String, String>>,
        user: String?,
        attachments: List<Attachment> = emptyList(),
    ): Flow<StreamEvent> = flow {
        val body = buildChatCompletionsBody(model, messages, user, attachments)
        val req = buildSseRequest(
            path = "v1/chat/completions",
            body = body,
        )
        sseClient.stream(req).collect { payload ->
            val event = parseNonHermesEvent(payload)
            if (event != null) emit(event)
        }
        emit(StreamEvent.Completed())
    }

    /**
     * HERMES 流式发送：先 POST /v1/runs 拿 run_id，再开 SSE 流。
     *
     * @param history 本轮之前的对话历史（user/assistant 消息，不含本轮 input）。
     *   Hermes run 无状态，必须显式传先前消息否则 "上面那个" 等指代无上下文。
     * @param attachments 本轮 input 的附件（对齐 web sendHermesRun 顶层 attachments 字段）。
     */
    fun streamHermesRun(
        agentId: String,
        sessionId: String,
        engineType: String?,
        model: String?,
        input: String,
        history: List<HistoryItem> = emptyList(),
        user: String? = null,
        attachments: List<Attachment> = emptyList(),
    ): Flow<StreamEvent> = flow {
        val startResp = try {
            gatewayApi.startRun(
                StartRunRequest(
                    session_id = sessionId,
                    input = input,
                    model = model,
                    conversationHistory = history,
                    user = user,
                    attachments = attachments,
                )
            )
        } catch (e: Throwable) {
            emit(StreamEvent.Failed("启动运行失败：${e.message ?: ""}"))
            return@flow
        }
        val runId = startResp.runId
        emit(StreamEvent.RunStarted(runId))
        pendingRunStore.registerPendingRun(runId, sessionId, agentId)
        val req = buildSseRequest(
            path = "v1/runs/${runId}/events",
            body = "",
            methodGet = true,
        )
        try {
            sseClient.stream(req).collect { payload ->
                val event = parseHermesEvent(payload)
                if (event != null) emit(event)
            }
        } finally {
            // run.completed/failed/cancelled 时清；流中断时不清（等 resumePendingRuns 处理）
            // 这里 conservative：流结束时若没显式 Completed/Failed/Cancelled，就不清
        }
    }

    private fun buildChatCompletionsBody(
        model: String,
        messages: List<Pair<String, String>>,
        user: String?,
        attachments: List<Attachment>,
    ): String {
        // 对齐 apps/enduser useChat.ts toEngineMessage：附件挂到本轮 user 消息上（messages 数组里最后一条 user）。
        // 找到最后一条 user 的 index，把它从 {role, content} 升级为 {role, content, attachments}。
        val lastUserIdx = messages.indexOfLast { it.first == "user" }
        val msgs = messages.mapIndexed { idx, (role, content) ->
            if (idx == lastUserIdx && attachments.isNotEmpty()) {
                val atts = attachmentsToJsonArray(attachments)
                """{"role":"$role","content":${jsonPrimitive(content)},"attachments":$atts}"""
            } else {
                """{"role":"$role","content":${jsonPrimitive(content)}}"""
            }
        }
        val userPart = user?.let { ""","user":${jsonPrimitive(it)}""" } ?: ""
        return """{"model":${jsonPrimitive(model)},"messages":[$msgs],"stream":true,"stream_options":{"include_usage":true}$userPart}"""
    }

    private fun attachmentsToJsonArray(atts: List<Attachment>): String =
        atts.joinToString(",", prefix = "[", postfix = "]") { a ->
            val name = JsonPrimitive(a.name).toString()
            val path = JsonPrimitive(a.path).toString()
            val isImage = JsonPrimitive(a.isImage).toString()
            """{"name":$name,"path":$path,"is_image":$isImage}"""
        }

    private fun jsonPrimitive(s: String): String = JsonPrimitive(s).toString()

    private fun buildSseRequest(path: String, body: String, methodGet: Boolean = false): Request {
        val url = serverConfig.gatewayUrl.trimEnd('/') + "/" + path.trimStart('/')
        val builder = Request.Builder().url(url)
        builder.header("Content-Type", "application/json")
        builder.header("Accept", "text/event-stream")
        // X-Agent-ID / X-Session-ID / X-Engine-Type 由 AgentHeaderInterceptor 注入
        if (methodGet) {
            builder.get()
        } else {
            val mediaType = "application/json".toMediaType()
            builder.post(body.toRequestBody(mediaType))
        }
        return builder.build()
    }

    // ── 非 HERMES：OpenAI 风格 choices[0].delta + 自定义 type ──
    private fun parseNonHermesEvent(payload: String): StreamEvent? =
        StreamEventParser.parseNonHermes(payload, json)

    // ── HERMES：event 字段路由 ──
    private fun parseHermesEvent(payload: String): StreamEvent? =
        StreamEventParser.parseHermes(payload, json)
}
