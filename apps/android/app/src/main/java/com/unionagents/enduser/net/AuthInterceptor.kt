package com.unionagents.enduser.net

import okhttp3.Interceptor
import okhttp3.Response
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 给所有请求附加 `Authorization: Bearer <accessToken>`（如果请求未自带）。
 * 镜像 apps/enduser/src/api/client.ts 的 headers["Authorization"] 逻辑。
 */
@Singleton
class AuthInterceptor @Inject constructor(
    private val tokenStorage: TokenStorage,
) : Interceptor {

    override fun intercept(chain: Interceptor.Chain): Response {
        val req = chain.request()
        if (req.header("Authorization") != null) return chain.proceed(req)
        val token = tokenStorage.getAccessTokenBlocking()
        val authedReq = if (token != null) {
            req.newBuilder().header("Authorization", "Bearer $token").build()
        } else req
        return chain.proceed(authedReq)
    }
}

private fun TokenStorage.getAccessTokenBlocking(): String? =
    kotlinx.coroutines.runBlocking { getAccessToken() }
