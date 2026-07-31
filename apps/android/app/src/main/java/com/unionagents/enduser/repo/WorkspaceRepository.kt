package com.unionagents.enduser.repo

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import com.unionagents.enduser.net.ManagerApi
import com.unionagents.enduser.net.dto.CreateFolderRequest
import com.unionagents.enduser.net.dto.MoveFileRequest
import com.unionagents.enduser.net.dto.UploadFileResponse
import com.unionagents.enduser.net.dto.WorkspaceFileContent
import com.unionagents.enduser.net.dto.WorkspaceFileEntry
import com.unionagents.enduser.net.dto.WorkspaceFileList
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.toRequestBody
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class WorkspaceRepository @Inject constructor(
    private val managerApi: ManagerApi,
    @ApplicationContext private val context: Context,
) {
    suspend fun listFiles(agentId: String, path: String): WorkspaceFileList {
        val resp = managerApi.listFiles(agentId, path)
        // 排序：目录在前，再按名（与 Vue ChatFileBrowser 一致）
        val sorted = resp.entries.sortedWith(compareBy({ !it.isDir }, { it.name }))
        return resp.copy(entries = sorted)
    }

    suspend fun readFile(agentId: String, path: String): WorkspaceFileContent =
        managerApi.readFile(agentId, path)

    suspend fun downloadFile(agentId: String, path: String): ByteArray =
        managerApi.downloadFile(agentId, path).bytes()

    /**
     * 上传文件到工作区指定目录。
     * @param dir 相对工作区根的目标目录（如 "."、"uploads"、"folder/sub"）
     */
    suspend fun uploadFile(agentId: String, dir: String, uri: Uri): UploadFileResponse =
        withContext(Dispatchers.IO) {
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
            managerApi.uploadAgentFile(agentId, dir, part)
        }

    suspend fun createFolder(agentId: String, parentPath: String, name: String) {
        managerApi.createFolder(agentId, parentPath, CreateFolderRequest(name))
    }

    suspend fun deleteFile(agentId: String, path: String) {
        managerApi.deleteFile(agentId, path)
    }

    suspend fun moveFile(agentId: String, fromPath: String, toPath: String) {
        managerApi.moveFile(agentId, MoveFileRequest(fromPath, toPath))
    }

    private fun queryDisplayName(uri: Uri): String? {
        val cursor = context.contentResolver.query(uri, null, null, null, null) ?: return null
        cursor.use {
            val idx = it.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if (idx < 0 || !it.moveToFirst()) return null
            return it.getString(idx)
        }
    }

    fun List<WorkspaceFileEntry>.dirsFirst(): List<WorkspaceFileEntry> =
        sortedWith(compareBy({ !it.isDir }, { it.name }))
}
