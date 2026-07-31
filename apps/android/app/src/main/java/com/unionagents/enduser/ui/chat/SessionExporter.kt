package com.unionagents.enduser.ui.chat

import com.unionagents.enduser.net.dto.Message
import com.unionagents.enduser.net.dto.Session
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * 会话导出工具：JSON / Markdown 转录。镜像 web ChatPage.vue 的 downloadTranscript + exportSessionJSON。
 */
object SessionExporter {

    private val json = Json { prettyPrint = true }

    /** 紧凑 JSON，便于跨设备/再次导入。 */
    fun toJson(session: Session, messages: List<Message>): String {
        val messagesArr = buildJsonArray {
            messages.forEach { m ->
                add(buildJsonObject {
                    put("role", m.role)
                    put("content", m.content ?: "")
                    if (m.attachments.isNotEmpty()) {
                        put("attachments", buildJsonArray {
                            m.attachments.forEach { att ->
                                add(buildJsonObject {
                                    put("name", att.name)
                                    put("path", att.path)
                                    put("is_image", att.isImage)
                                    att.mime?.let { put("mime", it) }
                                    att.size?.let { put("size", it) }
                                    att.thumbnailUrl?.let { put("thumbnail_url", it) }
                                })
                            }
                        })
                    }
                })
            }
        }
        val obj = buildJsonObject {
            put("session_id", session.stableId)
            session.title?.let { put("title", it) }
            session.model?.let { put("model", it) }
            put("exported_at", System.currentTimeMillis())
            put("messages", messagesArr)
        }
        return json.encodeToString(JsonObject.serializer(), obj)
    }

    /** Markdown 转录，每条消息 ## role + 内容，消息间用 --- 分隔。 */
    fun toTranscript(session: Session, messages: List<Message>): String {
        // title 可空，stableTitle 兜底返回「未命名」；导出时优先用 raw title，否则用 stableId
        val title = session.title?.ifBlank { null } ?: session.stableId.ifBlank { "session" }
        val sb = StringBuilder()
        sb.appendLine("# $title")
        sb.appendLine()
        session.model?.let {
            sb.appendLine("> model: $it")
            sb.appendLine()
        }
        sb.appendLine("> exported_at: ${java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", java.util.Locale.CHINA).format(java.util.Date())}")
        sb.appendLine()
        sb.appendLine("---")
        sb.appendLine()
        messages.forEach { m ->
            val role = when (m.role.lowercase()) {
                "user" -> "用户"
                "assistant" -> "助手"
                "tool" -> "工具"
                "system" -> "系统"
                else -> m.role
            }
            sb.appendLine("## $role")
            sb.appendLine()
            sb.appendLine(m.content ?: "(无内容)")
            if (m.attachments.isNotEmpty()) {
                sb.appendLine()
                sb.appendLine("**附件：**")
                m.attachments.forEach { att ->
                    val tag = if (att.isImage) "图" else "文件"
                    sb.appendLine("- [$tag] ${att.name}")
                }
            }
            sb.appendLine()
            sb.appendLine("---")
            sb.appendLine()
        }
        return sb.toString()
    }

    /** 文件名安全化：去空白/特殊字符。 */
    fun safeFilename(title: String, fallback: String): String {
        val raw = title.ifBlank { fallback }
        return raw.replace(Regex("[\\\\/:*?\"<>|]"), "_")
            .replace(Regex("\\s+"), "_")
            .take(40)
            .ifBlank { fallback }
    }
}
