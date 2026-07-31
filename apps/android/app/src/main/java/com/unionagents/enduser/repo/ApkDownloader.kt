package com.unionagents.enduser.repo

import android.app.DownloadManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.Uri
import android.widget.Toast
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton
import java.io.File

@Singleton
class ApkDownloader @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    private var downloadId: Long = -1
    private var receiver: BroadcastReceiver? = null

    fun startDownload(url: String, version: String): Long {
        try {
            ensureReceiver()
            val request = DownloadManager.Request(Uri.parse(url)).apply {
                setTitle("知行 $version")
                setDescription("正在下载更新")
                setDestinationInExternalFilesDir(
                    context,
                    android.os.Environment.DIRECTORY_DOWNLOADS,
                    "zhixing-$version.apk",
                )
                setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                setMimeType("application/vnd.android.package-archive")
            }
            val dm = context.getSystemService(DownloadManager::class.java)
                ?: run {
                    Toast.makeText(context, "下载服务不可用", Toast.LENGTH_SHORT).show()
                    return -1
                }
            downloadId = dm.enqueue(request)
            Toast.makeText(context, "开始下载，完成后将自动弹出安装", Toast.LENGTH_LONG).show()
            return downloadId
        } catch (e: Exception) {
            Toast.makeText(context, "下载失败：${e.message ?: "未知错误"}", Toast.LENGTH_LONG).show()
            return -1
        }
    }

    fun queryProgress(id: Long): Float? {
        if (id <= 0) return null
        val dm = context.getSystemService(DownloadManager::class.java) ?: return null
        val cursor = dm.query(DownloadManager.Query().setFilterById(id)) ?: return null
        try {
            if (!cursor.moveToFirst()) return null
            val status = cursor.getInt(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS))
            if (status == DownloadManager.STATUS_FAILED) return -1f
            if (status == DownloadManager.STATUS_SUCCESSFUL) return 1f
            val soFar = cursor.getLong(
                cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_BYTES_DOWNLOADED_SO_FAR)
            )
            val total = cursor.getLong(
                cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_TOTAL_SIZE_BYTES)
            )
            if (total <= 0) return 0f
            return (soFar.toFloat() / total).coerceIn(0f, 1f)
        } finally {
            cursor.close()
        }
    }

    private fun ensureReceiver() {
        if (receiver != null) return
        val r = object : BroadcastReceiver() {
            override fun onReceive(c: Context?, intent: Intent?) {
                val id = intent?.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1L) ?: -1L
                if (id != downloadId) return
                triggerInstall()
            }
        }
        // DownloadManager 广播来自系统进程，必须 RECEIVER_EXPORTED 才能投递
        ContextCompat.registerReceiver(
            context,
            r,
            IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE),
            ContextCompat.RECEIVER_EXPORTED,
        )
        receiver = r
    }

    private fun triggerInstall() {
        try {
            val dm = context.getSystemService(DownloadManager::class.java) ?: return
            val cursor = dm.query(DownloadManager.Query().setFilterById(downloadId)) ?: return
            try {
                if (!cursor.moveToFirst()) {
                    Toast.makeText(context, "下载失败：找不到记录", Toast.LENGTH_LONG).show()
                    return
                }
                val status = cursor.getInt(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS))
                if (status != DownloadManager.STATUS_SUCCESSFUL) {
                    val reason = cursor.getInt(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_REASON))
                    Toast.makeText(context, "下载失败（reason=$reason）", Toast.LENGTH_LONG).show()
                    return
                }
                val localUriStr = cursor.getString(
                    cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_LOCAL_URI)
                ) ?: return
                val path = Uri.parse(localUriStr).path ?: return
                val file = File(path)
                if (!file.exists()) {
                    Toast.makeText(context, "下载文件不存在", Toast.LENGTH_LONG).show()
                    return
                }
                val contentUri = FileProvider.getUriForFile(
                    context,
                    "${context.packageName}.fileprovider",
                    file,
                )
                val intent = Intent(Intent.ACTION_VIEW).apply {
                    setDataAndType(contentUri, "application/vnd.android.package-archive")
                    flags = Intent.FLAG_ACTIVITY_NEW_TASK
                    addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                }
                Toast.makeText(context, "下载完成，正在打开安装器", Toast.LENGTH_SHORT).show()
                context.startActivity(intent)
            } finally {
                cursor.close()
            }
        } catch (e: Exception) {
            Toast.makeText(context, "安装失败：${e.message ?: "未知错误"}", Toast.LENGTH_LONG).show()
        }
    }
}
