package com.unionagents.enduser.repo

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import com.unionagents.enduser.net.GatewayApi
import com.unionagents.enduser.net.ManagerApi
import com.unionagents.enduser.net.dto.ApprovalRequest
import com.unionagents.enduser.net.dto.Attachment
import com.unionagents.enduser.net.dto.CreateSessionRequest
import com.unionagents.enduser.net.dto.Message
import com.unionagents.enduser.net.dto.Session
import com.unionagents.enduser.net.dto.UpdateTitleRequest
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.InputStream
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class ChatRepository @Inject constructor(
    private val gatewayApi: GatewayApi,
    private val managerApi: ManagerApi,
    @ApplicationContext private val context: Context,
) {
    suspend fun listSessions(limit: Int = 50): List<Session> {
        val resp = gatewayApi.listSessions(limit)
        return resp.sessions ?: resp.data ?: emptyList()
    }

    suspend fun createSession(model: String?): Session {
        val resp = gatewayApi.createSession(CreateSessionRequest(model = model))
        return resp.session ?: Session()
    }

    suspend fun updateTitle(sessionId: String, title: String) {
        gatewayApi.updateSession(sessionId, UpdateTitleRequest(title))
    }

    suspend fun deleteSession(sessionId: String) {
        gatewayApi.deleteSession(sessionId)
    }

    suspend fun listMessages(sessionId: String): List<Message> {
        val resp = gatewayApi.listMessages(sessionId)
        val list = resp.messages ?: resp.data ?: emptyList()
        // 服务端在入库前把 attachments 字段剥掉、把路径嵌进 content 作为 [Attached files: ...] 提示。
        // 这里从 content 还原 attachments + 把提示文本从显示内容里去掉，让历史消息图片能渲染。
        return list.filter { it.isVisible }.map { AttachmentHintParser.restore(it) }
    }

    suspend fun submitApproval(runId: String, choice: String) {
        gatewayApi.submitApproval(runId, ApprovalRequest(choice = choice))
    }

    /**
     * 构造一个本地占位 Attachment：含 localUri + name + mime + isImage，path 留空。
     * 用于消息发送时 UI 立即显示用户附件（缩略图、文件名），上传完成后再回填真实 path。
     */
    suspend fun buildLocalAttachment(uri: Uri): Attachment = withContext(Dispatchers.IO) {
        val displayName = queryDisplayName(uri) ?: "upload-${System.currentTimeMillis()}"
        val mime = context.contentResolver.getType(uri) ?: "application/octet-stream"
        Attachment(
            name = displayName,
            path = "",
            isImage = mime.startsWith("image/"),
            mime = mime,
            localUri = uri.toString(),
        )
    }

    /**
     * 从后端下载附件原始字节（用于历史消息加载时渲染图片缩略图）。
     * 走 manager /agent-instances/{id}/files/download?path=...，OkHttp 自动带 JWT。
     */
    suspend fun downloadAttachmentBytes(agentId: String, path: String): ByteArray = withContext(Dispatchers.IO) {
        managerApi.downloadFile(agentId, path).bytes()
    }

    /**
     * 上传单个附件到用户 profile 工作区 uploads/ 目录。
     * 返回引擎可消费的 Attachment（含 path，引擎据此读文件）。
     */
    suspend fun uploadAttachment(agentId: String, uri: Uri): Attachment = withContext(Dispatchers.IO) {
        val resolver = context.contentResolver
        val displayName = queryDisplayName(uri) ?: "upload-${System.currentTimeMillis()}"
        val mime = resolver.getType(uri) ?: "application/octet-stream"
        val bytes = (resolver.openInputStream(uri)?.use { it.readBytes() })
            ?: error("无法读取文件")
        val part = MultipartBody.Part.createFormData(
            name = "file",
            filename = displayName,
            body = bytes.toRequestBody(mime.toMediaTypeOrNull()),
        )
        val resp = managerApi.uploadAgentFile(agentId, "uploads", part)
        Attachment(
            name = resp.filename ?: displayName,
            path = resp.path ?: error("上传失败：未返回 path"),
            isImage = resp.isImage,
            mime = resp.mime ?: mime,
            size = resp.size ?: bytes.size.toLong(),
            localUri = uri.toString(),
        )
    }

    private fun queryDisplayName(uri: Uri): String? {
        val cursor = context.contentResolver.query(uri, null, null, null, null) ?: return null
        cursor.use {
            val idx = it.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if (idx < 0 || !it.moveToFirst()) return null
            return it.getString(idx)
        }
    }

    /**
     * 把文本写入 SAF 返回的 Uri（用户在系统文件选择器选定的位置）。
     * 用于会话导出 JSON / Markdown 转录。
     */
    suspend fun writeTextToUri(uri: Uri, text: String) = withContext(Dispatchers.IO) {
        context.contentResolver.openOutputStream(uri, "w")?.use { out ->
            out.write(text.toByteArray(Charsets.UTF_8))
        } ?: error("无法写入文件")
    }
}

