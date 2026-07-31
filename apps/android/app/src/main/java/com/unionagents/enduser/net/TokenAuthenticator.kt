package com.unionagents.enduser.net

import okhttp3.Authenticator
import okhttp3.Request
import okhttp3.Response
import okhttp3.Route
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 401 时自动 refresh + retry once（OkHttp 普通 Retrofit 请求用，**不**适用 SSE）。
 * 内部委托给 [TokenRefresher] —— 单飞 Mutex 避免并发 401 触发多次 refresh。
 *
 * refresh 失败（refresh_token 也过期）时调 [SessionController.emitForceLogout] 触发全局跳登录。
 */
@Singleton
class TokenAuthenticator @Inject constructor(
    private val tokenStorage: TokenStorage,
    private val tokenRefresher: TokenRefresher,
    private val sessionController: SessionController,
) : Authenticator {

    override fun authenticate(route: Route?, response: Response): Request? {
        val reqToken = response.request.header("Authorization")?.removePrefix("Bearer ")?.trim()
        val newToken = kotlinx.coroutines.runBlocking {
            // 单飞 Mutex 已在 TokenRefresher.refresh() 内 —— 这里额外比对避免重复
            val currentAccess = tokenStorage.getAccessToken()
            if (currentAccess != null && currentAccess != reqToken) {
                return@runBlocking currentAccess
            }
            tokenRefresher.refresh()
        }
        if (newToken == null) {
            sessionController.emitForceLogout()
            return null
        }
        return response.request.newBuilder()
            .header("Authorization", "Bearer $newToken")
            .build()
    }
}
