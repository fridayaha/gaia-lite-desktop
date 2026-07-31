package com.unionagents.enduser.net.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class WorkspaceFileEntry(
    val name: String,
    val path: String,
    @SerialName("is_dir") val isDir: Boolean = false,
    val size: Long = 0,
    @SerialName("mtime_ns") val mtimeNs: Long? = null,
    @SerialName("is_text") val isText: Boolean? = null,
)

@Serializable
data class WorkspaceFileList(
    val entries: List<WorkspaceFileEntry> = emptyList(),
    val path: String = "",
    val error: String? = null,
)

@Serializable
data class WorkspaceFileContent(
    val path: String,
    val name: String,
    val size: Long,
    val truncated: Boolean = false,
    @SerialName("is_text") val isText: Boolean = false,
    val content: String? = null,
    @SerialName("content_b64") val contentB64: String? = null,
    @SerialName("is_image") val isImage: Boolean = false,
    @SerialName("is_markdown") val isMarkdown: Boolean = false,
    @SerialName("max_bytes") val maxBytes: Long = 0,
    val error: String? = null,
)

@Serializable
data class CreateFolderRequest(
    val name: String,
)

@Serializable
data class MoveFileRequest(
    @SerialName("from_path") val fromPath: String,
    @SerialName("to_path") val toPath: String,
)
