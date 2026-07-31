package com.unionagents.enduser.net

import android.content.Context
import com.unionagents.enduser.BuildConfig
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 运行期后端地址读取器。
 *
 * 优先读 APK 内 `assets/server_config.json`——该文件在 patch 阶段被替换成
 * 当前 ECS 的真实 URL（占位符 `__UA_MANAGER_URL__` / `__UA_GATEWAY_URL__` 被替换）。
 *
 * asset 仍是占位符（未 patch）或读取失败时，回退到 [BuildConfig.MANAGER_BASE_URL] /
 * [BuildConfig.GATEWAY_BASE_URL] 兜底——保证未 patch 的 debug APK 也能跑通。
 *
 * 单例 + `@Volatile` 缓存：asset 编译进 APK 后运行期不变，进程内只读一次。
 */
@Singleton
class ServerConfig @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    @Volatile
    private var cached: ServerConfigResolver.Resolved? = null

    val managerUrl: String
        get() = load().managerUrl

    val gatewayUrl: String
        get() = load().gatewayUrl

    private fun load(): ServerConfigResolver.Resolved {
        cached?.let { return it }
        val raw = try {
            context.assets.open("server_config.json").bufferedReader().use { it.readText() }
        } catch (e: Exception) {
            null
        }
        val resolved = ServerConfigResolver.resolve(
            raw = raw,
            managerDefault = BuildConfig.MANAGER_BASE_URL,
            gatewayDefault = BuildConfig.GATEWAY_BASE_URL,
        )
        cached = resolved
        return resolved
    }
}
