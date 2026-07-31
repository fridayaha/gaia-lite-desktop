package com.unionagents.enduser.repo

import com.unionagents.enduser.net.dto.Attachment
import com.unionagents.enduser.net.dto.Message

/**
 * 服务端把 messages 存到 Hermes 前会把 attachments 字段剥掉、把路径嵌进 content 作为
 * ``[Attached files: path1, path2]`` 提示（见 services/gateway/app/attachment_hint.py）。
 * 历史消息从服务端拉回时没有 attachments 字段，本工具把 hint 解析回结构化附件、
 * 并把提示文本从显示内容里剥掉，让聊天框能渲染图片缩略图。
 */
object AttachmentHintParser {

    private val HINT_REGEX = Regex("""\[Attached files:\s*([^\]]+)\]""")

    /**
     * 若 message.content 里含 ``[Attached files: ...]`` hint 且 attachments 为空：
     * - 解析出 Attachment 列表（path / name / isImage 从扩展名推断）
     * - 把 hint 从显示文本中剥掉（连前面多余的空行一起去掉）
     * 其它情况原样返回。
     */
    fun restore(msg: Message): Message {
        val content = msg.content ?: return msg
        if (msg.attachments.isNotEmpty()) return msg
        val match = HINT_REGEX.find(content) ?: return msg
        val paths = match.groupValues[1]
            .split(",")
            .map { it.trim() }
            .filter { it.isNotEmpty() }
        if (paths.isEmpty()) return msg
        val attachments = paths.map { p ->
            Attachment(
                name = p.substringAfterLast('/'),
                path = p,
                isImage = isImagePath(p),
            )
        }
        val displayContent = content
            .replace(match.value, "")
            .replace(Regex("""\n{2,}$"""), "")
            .trimEnd()
        return msg.copy(content = displayContent, attachments = attachments)
    }

    internal fun isImagePath(path: String): Boolean {
        val lower = path.lowercase()
        return lower.endsWith(".jpg") || lower.endsWith(".jpeg") ||
            lower.endsWith(".png") || lower.endsWith(".gif") ||
            lower.endsWith(".webp") || lower.endsWith(".bmp") ||
            lower.endsWith(".heic") || lower.endsWith(".heif") ||
            lower.endsWith(".svg")
    }
}
