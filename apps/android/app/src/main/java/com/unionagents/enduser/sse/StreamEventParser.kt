package com.unionagents.enduser.sse

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/**
 * SSE payload → [StreamEvent] 解析。
 * 抽成独立对象便于单测 —— 镜像 apps/enduser/src/composables/useChat.ts 的 handleHermesEvent + chat-completions 帧解析。
 */
internal object StreamEventParser {

    /**
     * 非 HERMES（OpenAI 风格 chat-completions）帧解析。
     */
    fun parseNonHermes(payload: String, json: Json): StreamEvent? {
        val obj = try {
            json.parseToJsonElement(payload).jsonObject
        } catch (_: Throwable) {
            return null
        }
        parseSilenceHint(obj)?.let { return it }
        // 工具进度（p.tool + p.status）
        val tool = obj["tool"]?.jsonPrimitive?.contentOrNull
        val status = obj["status"]?.jsonPrimitive?.contentOrNull
        if (tool != null && status != null) {
            val label = obj["label"]?.jsonPrimitive?.contentOrNull ?: ""
            val toolCallId = obj["toolCallId"]?.jsonPrimitive?.contentOrNull
            return when (status) {
                "running" -> StreamEvent.ToolStarted(tool, label, toolCallId)
                "completed", "success" -> StreamEvent.ToolCompleted(
                    name = tool,
                    error = parseToolError(obj["error"]),
                    result = obj["result"]?.jsonPrimitive?.contentOrNull,
                )
                else -> null
            }
        }
        // run 生命周期 + 审批
        val type = obj["type"]?.jsonPrimitive?.contentOrNull
        if (type == "run.start") {
            val runId = obj["run_id"]?.jsonPrimitive?.contentOrNull ?: return null
            return StreamEvent.RunStarted(runId)
        }
        if (type == "approval.request") {
            return StreamEvent.ApprovalRequested(
                runId = obj["run_id"]?.jsonPrimitive?.contentOrNull ?: "",
                command = obj["command"]?.jsonPrimitive?.contentOrNull ?: "",
                description = obj["description"]?.jsonPrimitive?.contentOrNull ?: "",
                choices = obj["choices"]?.jsonArray?.mapNotNull { it.jsonPrimitive.contentOrNull } ?: listOf("once", "session", "always", "deny"),
            )
        }
        if (type == "approval.responded") {
            val choice = obj["choice"]?.jsonPrimitive?.contentOrNull ?: return null
            return StreamEvent.ApprovalResponded(choice)
        }
        // 常规 chat-completions 帧
        val choices = obj["choices"] as? JsonArray
        val delta = choices?.firstOrNull()?.let { (it as? JsonObject)?.get("delta") as? JsonObject }
        val content = delta?.get("content")?.jsonPrimitive?.contentOrNull
        val reasoning = delta?.get("reasoning_content")?.jsonPrimitive?.contentOrNull
            ?: delta?.get("reasoning")?.jsonPrimitive?.contentOrNull
        val usage = obj["usage"]
        if (content != null) return StreamEvent.ContentDelta(content)
        if (reasoning != null) return StreamEvent.ReasoningDelta(reasoning)
        if (usage != null) return StreamEvent.Completed(usage = usage)
        return null
    }

    /**
     * HERMES event 帧（payload 是 JSON 对象，含 `event` 字段路由）。
     */
    fun parseHermes(payload: String, json: Json): StreamEvent? {
        val obj = try {
            json.parseToJsonElement(payload).jsonObject
        } catch (_: Throwable) {
            return null
        }
        val event = obj["event"]?.jsonPrimitive?.contentOrNull ?: return null
        parseSilenceHint(obj)?.let { return it }
        return when (event) {
            "run.started" -> StreamEvent.RunStarted(obj["run_id"]?.jsonPrimitive?.contentOrNull ?: "")
            "message.delta" -> {
                val delta = obj["delta"]?.jsonPrimitive?.contentOrNull ?: ""
                if (delta.isNotEmpty()) StreamEvent.HermesDelta(delta) else null
            }
            "reasoning.available" -> {
                val text = obj["text"]?.jsonPrimitive?.contentOrNull ?: ""
                if (text.isNotEmpty()) StreamEvent.HermesReasoning(text) else null
            }
            "tool.started" -> StreamEvent.ToolStarted(
                name = obj["tool"]?.jsonPrimitive?.contentOrNull ?: "tool",
                preview = obj["preview"]?.jsonPrimitive?.contentOrNull ?: "",
                toolCallId = obj["toolCallId"]?.jsonPrimitive?.contentOrNull,
            )
            "tool.completed" -> StreamEvent.ToolCompleted(
                name = obj["tool"]?.jsonPrimitive?.contentOrNull ?: "tool",
                error = parseToolError(obj["error"]),
                result = obj["result"]?.jsonPrimitive?.contentOrNull
                    ?: obj["output"]?.jsonPrimitive?.contentOrNull,
            )
            "approval.request" -> StreamEvent.ApprovalRequested(
                runId = obj["run_id"]?.jsonPrimitive?.contentOrNull ?: "",
                command = obj["command"]?.jsonPrimitive?.contentOrNull ?: "",
                description = obj["description"]?.jsonPrimitive?.contentOrNull ?: "",
                choices = obj["choices"]?.jsonArray?.mapNotNull { it.jsonPrimitive.contentOrNull } ?: listOf("once", "session", "always", "deny"),
            )
            "approval.responded" -> {
                val choice = obj["choice"]?.jsonPrimitive?.contentOrNull ?: return null
                StreamEvent.ApprovalResponded(choice)
            }
            "run.completed" -> StreamEvent.Completed(
                usage = obj["usage"],
                output = obj["output"]?.jsonPrimitive?.contentOrNull,
            )
            "run.failed" -> StreamEvent.Failed(obj["error"]?.jsonPrimitive?.contentOrNull ?: "运行失败")
            "run.cancelled" -> StreamEvent.Cancelled
            else -> null
        }
    }

    /**
     * gateway 静默看门狗提示帧（event=gateway.silence，data 含 elapsed 秒数）。
     * 引擎无关，两条解析路径共用；非提示帧返回 null。
     */
    private fun parseSilenceHint(obj: JsonObject): StreamEvent? {
        if (obj["event"]?.jsonPrimitive?.contentOrNull != "gateway.silence") return null
        return StreamEvent.SilenceHint(
            elapsedSeconds = obj["elapsed"]?.jsonPrimitive?.contentOrNull?.toIntOrNull() ?: 0,
        )
    }

    /**
     * 从 SSE tool.completed 事件抽取 error 字段。
     * - Hermes: 发 boolean is_error 标志（true=失败，false=成功），SSE 不带实际错误信息 → 返回占位文案
     * - 非 Hermes: 可能发 string 错误信息 → 原样保留
     * - null / JsonNull: 成功，返回 null
     */
    private fun parseToolError(elem: JsonElement?): String? {
        if (elem == null || elem is JsonNull) return null
        val prim = elem.jsonPrimitive
        return when {
            prim.isString -> prim.contentOrNull
            prim.boolean -> "工具执行失败"
            else -> null
        }
    }
}
