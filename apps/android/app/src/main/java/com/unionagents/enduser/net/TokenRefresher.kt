package com.unionagents.enduser.net

import com.unionagents.enduser.di.RefreshClient
import com.unionagents.enduser.net.dto.RefreshRequest
import com.unionagents.enduser.net.dto.RefreshResponse
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import javax.inject.Inject
import javax.inject.Named
import javax.inject.Singleton

/**
 * Token refresh 单飞工具 —— 同时给 OkHttp Authenticator（普通请求 401）和 SseClient（SSE 401）使用。
 *
 * 单飞：并发 401 合并成一次 refresh（Mutex）。
 * refresh 请求走 [RefreshClient]（无 Authenticator，避免循环）。
 * 镜像 apps/enduser/src/api/auth.ts 的 ensureAuthenticated + refresh 单飞。
 */
@Singleton
class TokenRefresher @Inject constructor(
    private val tokenStorage: TokenStorage,
    private val json: Json,
    @RefreshClient private val refreshClient: OkHttpClient,
    @Named("manager_base_url") private val managerBaseUrl: String,
) : TokenRefreshProvider {
    private val mutex = Mutex()

    /**
     * 同步刷新一次 access token；返回新 access token，失败返回 null。
     * 调用方拿到新 token 后自己 retry 原请求。
     */
    override suspend fun refresh(): String? = mutex.withLock {
        val prevRefresh = tokenStorage.getRefreshToken() ?: return@withLock null
        refreshOnce(prevRefresh)
    }

    private suspend fun refreshOnce(refreshToken: String): String? {
        val body = json.encodeToString(RefreshRequest.serializer(), RefreshRequest(refreshToken))
        val req = Request.Builder()
            .url("${managerBaseUrl}auth/refresh")
            .post(body.toRequestBody("application/json".toMediaType()))
            .build()
        return runCatching {
            refreshClient.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return@use null
                val raw = resp.body?.string() ?: return@use null
                val parsed = json.decodeFromString(RefreshResponse.serializer(), raw)
                tokenStorage.save(TokenData(parsed.accessToken, parsed.refreshToken ?: refreshToken))
                parsed.accessToken
            }
        }.getOrNull()
    }
}
