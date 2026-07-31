package com.unionagents.enduser.ui.workspace

import com.unionagents.enduser.net.dto.WorkspaceFileContent
import java.util.Base64

enum class PreviewType {
    Image,
    Markdown,
    Html,
    Text,
    Pdf,
    Office,
    Unknown,
}

fun WorkspaceFileContent.previewType(): PreviewType {
    if (isImage) return PreviewType.Image
    if (isMarkdown) return PreviewType.Markdown
    val ext = name.fileExtension()
    return when (ext) {
        "md", "markdown", "mkd" -> PreviewType.Markdown
        "html", "htm" -> PreviewType.Html
        "pdf" -> PreviewType.Pdf
        "doc", "docx", "xls", "xlsx", "ppt", "pptx" -> PreviewType.Office
        else -> if (isText || content != null || contentB64 != null) PreviewType.Text else PreviewType.Unknown
    }
}

fun String.fileExtension(): String = lowercase().substringAfterLast('.', "")

fun String.mimeTypeForExtension(): String = when (fileExtension()) {
    "pdf" -> "application/pdf"
    "doc" -> "application/msword"
    "docx" -> "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    "xls" -> "application/vnd.ms-excel"
    "xlsx" -> "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    "ppt" -> "application/vnd.ms-powerpoint"
    "pptx" -> "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    "txt" -> "text/plain"
    "md", "markdown", "mkd" -> "text/markdown"
    "html", "htm" -> "text/html"
    "png" -> "image/png"
    "jpg", "jpeg" -> "image/jpeg"
    "gif" -> "image/gif"
    "webp" -> "image/webp"
    "svg" -> "image/svg+xml"
    else -> "application/octet-stream"
}

fun WorkspaceFileContent.decodeTextContent(): String {
    if (content != null) return content
    return runCatching {
        val b = Base64.getDecoder().decode(contentB64 ?: "")
        String(b, Charsets.UTF_8)
    }.getOrNull() ?: ""
}
