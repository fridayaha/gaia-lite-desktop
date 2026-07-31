package com.unionagents.enduser.net

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * 纯 Kotlin（无 Android 依赖）的 server_config.json 解析逻辑。
 *
 * 输入是 assets/server_config.json 的原始字符串（运行期由 [ServerConfig] 读取），
 * 占位符值（含 `__UA_` 子串）或解析失败时回退到 BuildConfig 兜底值。
 *
 * 拆成独立对象便于单测——[ServerConfig] 的 Android Context 依赖不直接测。
 */
object ServerConfigResolver {

    @Serializable
    data class Config(
        @SerialName("manager_url") val managerUrl: String = "",
        @SerialName("gateway_url") val gatewayUrl: String = "",
    )

    data class Resolved(val managerUrl: String, val gatewayUrl: String)

    private val json = Json { ignoreUnknownKeys = true }

    fun resolve(raw: String?, managerDefault: String, gatewayDefault: String): Resolved {
        val cfg = raw?.let {
            runCatching { json.decodeFromString(Config.serializer(), it) }.getOrNull()
        } ?: Config()
        return Resolved(
            managerUrl = cfg.managerUrl.takeIf { isReal(it) } ?: managerDefault,
            gatewayUrl = cfg.gatewayUrl.takeIf { isReal(it) } ?: gatewayDefault,
        )
    }

    private fun isReal(v: String): Boolean = v.isNotBlank() && !v.contains("__UA_")
}
