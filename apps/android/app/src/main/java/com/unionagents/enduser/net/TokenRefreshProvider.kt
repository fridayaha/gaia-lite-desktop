package com.unionagents.enduser.net

/**
 * Token refresh 能力的抽象 —— 让 [SessionController] 可单测（[TokenRefresher] 是非 open class，
 * 不能直接 mock；测试用 fake 实现此接口）。
 *
 * Hilt 绑定：见 [com.unionagents.enduser.di.NetworkModule] 的 `@Binds tokenRefreshProvider` 方法，
 * 把 [TokenRefresher] 绑定到本接口。
 */
interface TokenRefreshProvider {
    /**
     * 同步刷新一次 access token；返回新 access token，失败返回 null。
     */
    suspend fun refresh(): String?
}
