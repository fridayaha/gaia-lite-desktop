package com.unionagents.enduser.net

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import java.util.Base64

/**
 * JWT 解析工具 —— 纯 Kotlin（用 java.util.Base64 + kotlinx.serialization），
 * 便于单测，不依赖 Android 框架（org.json.JSONObject 在 unit test 里是 stub）。
 */
object JwtUtil {

    private val json = Json { ignoreUnknownKeys = true; isLenient = true }

    /**
     * 解析 JWT 的 exp 字段（UTC 秒级时间戳）。失败返回 null。
     * 接受三段式 JWT（header.payload.signature），payload 是 base64url 编码的 JSON。
     */
    fun parseExp(token: String): Long? {
        val parts = token.split(".")
        if (parts.size < 2) return null
        return runCatching {
            val payload = String(Base64.getUrlDecoder().decode(parts[1]))
            val obj = json.decodeFromString(JsonObject.serializer(), payload)
            obj["exp"]?.jsonPrimitive?.long?.takeIf { it > 0 }
        }.getOrNull()
    }
}
