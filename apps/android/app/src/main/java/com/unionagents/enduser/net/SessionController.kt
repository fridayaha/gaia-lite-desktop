package com.unionagents.enduser.net

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.launch
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 会话生命周期控制 —— app 启动时主动检查 access_token 是否快过期，过期则提前 refresh；
 * refresh 失败（refresh_token 也过期）时发 forceLogout 事件，UI 收到后跳登录页。
 *
 * 三处 forceLogout 触发点：
 *  - [SessionController.ensureSession]：app 启动时 access 快过期且 refresh 失败
 *  - [TokenAuthenticator]：普通请求 401 → refresh 失败
 *  - [SseClient]：SSE 路径 401 → refresh 失败（SSE 不经过 Authenticator）
 *
 * SharedFlow extraBufferCapacity=1 + tryEmit 保证幂等：多个触发点同时 emit，UI 端只跳一次。
 *
 * 镜像 apps/enduser/src/api/auth.ts 的 isAccessTokenValid + ensureAuthenticated + redirectToLogin。
 */
@Singleton
class SessionController @Inject constructor(
    private val tokenStorage: TokenStorage,
    private val tokenRefresher: TokenRefreshProvider,
) {
    private val scope = CoroutineScope(SupervisorJob())

    private val _forceLogout = MutableSharedFlow<Unit>(extraBufferCapacity = 1)
    val forceLogout: SharedFlow<Unit> = _forceLogout.asSharedFlow()

    fun emitForceLogout() {
        _forceLogout.tryEmit(Unit)
    }

    /**
     * App 启动时调用。access_token 距过期 <30s 则主动 refresh，失败则发 forceLogout。
     * access_token 解析失败（不是 JWT 或无 exp）→ 不处理，交给 401 兜底。
     */
    suspend fun ensureSession() {
        val access = tokenStorage.getAccessToken() ?: return
        val exp = JwtUtil.parseExp(access) ?: return
        val nowSec = System.currentTimeMillis() / 1000
        if (exp - nowSec < EXPIRY_THRESHOLD_SECONDS) {
            val refreshed = tokenRefresher.refresh()
            if (refreshed == null) emitForceLogout()
        }
    }

    fun ensureSessionAsync() {
        scope.launch { ensureSession() }
    }

    companion object {
        private const val EXPIRY_THRESHOLD_SECONDS = 30L
    }
}


