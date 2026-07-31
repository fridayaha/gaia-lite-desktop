package com.unionagents.enduser

import android.app.Application
import android.os.Build
import coil.ImageLoader
import coil.ImageLoaderFactory
import coil.decode.SvgDecoder
import coil.imageLoader
import com.unionagents.enduser.net.SessionController
import com.unionagents.enduser.repo.UpdateBadgeStore
import com.unionagents.enduser.sse.StreamProbe
import dagger.hilt.android.HiltAndroidApp
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import javax.inject.Inject

@HiltAndroidApp
class UnionAgentsApp : Application(), ImageLoaderFactory {

    @Inject
    lateinit var sessionController: SessionController

    @Inject
    lateinit var updateBadgeStore: UpdateBadgeStore

    override fun onCreate() {
        super.onCreate()
        installCrashDumper()
        StreamProbe.init(filesDir)
        StreamProbe.appStart("v=${BuildConfig.VERSION_NAME} device=${Build.MODEL} sdk=${Build.VERSION.SDK_INT}")
        sessionController.ensureSessionAsync()
        // 冷启动静默检查新版本：有更新时在「我的」/设置/检查更新三处打红点
        updateBadgeStore.refreshLatestAsync()
    }

    private fun installCrashDumper() {
        val defaultHandler = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
            try {
                val dir = File(filesDir, "crashes")
                dir.mkdirs()
                val file = File(dir, "last_crash.txt")
                val time = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.getDefault()).format(Date())
                file.writeText(buildString {
                    appendLine("Time: $time")
                    appendLine("Thread: ${thread.name}")
                    appendLine("Exception: ${throwable.javaClass.name}: ${throwable.message}")
                    appendLine("Stack trace:")
                    throwable.stackTrace.forEach { appendLine("  at $it") }
                    throwable.cause?.let { cause ->
                        appendLine("Caused by: ${cause.javaClass.name}: ${cause.message}")
                        cause.stackTrace.forEach { appendLine("  at $it") }
                    }
                })
            } catch (_: Throwable) {
                // 写入失败不影响原始崩溃处理
            }
            defaultHandler?.uncaughtException(thread, throwable)
        }
    }

    override fun newImageLoader(): ImageLoader =
        ImageLoader.Builder(this)
            .components {
                add(SvgDecoder.Factory())
            }
            .crossfade(true)
            .build()
}
