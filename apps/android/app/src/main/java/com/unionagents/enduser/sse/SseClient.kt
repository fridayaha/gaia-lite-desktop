package com.unionagents.enduser.sse

import com.unionagents.enduser.di.SseStreamingClient
import com.unionagents.enduser.net.SessionController
import com.unionagents.enduser.net.TokenRefresher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.isActive
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import java.io.IOException
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.coroutines.coroutineContext

/**
 * OkHttp 流式 SSE 读取 —— 不依赖第三方库，对齐 apps/enduser/src/composables/useChat.ts 的 ReadableStream + TextDecoder 行解析。
 *
 * 行级输出：每条 `data: <payload>` 行的 payload 部分（去掉前缀，去掉 `[DONE]` 终止符）。
 * 调用方负责 JSON 解析 + 路由到 StreamEvent。
 *
 * 401 处理：开流前显式检查响应码，401 时调用 [TokenRefresher].refresh() 重试一次（镜像 gwFetch）。
 * OkHttp Authenticator 不适用 SSE —— 流已开就不能 retry，必须在开流前处理。
 *
 * refresh 失败（refresh_token 也过期）时调 [SessionController.emitForceLogout] 触发全局跳登录，
 * 同时抛 IOException 让 ChatViewModel 停止流式状态。
 */
@Singleton
class SseClient @Inject constructor(
    @SseStreamingClient private val client: OkHttpClient,
    private val tokenRefresher: TokenRefresher,
    private val sessionController: SessionController,
) {
    /**
     * 开流，返回原始 payload flow。
     */
    fun stream(request: Request): Flow<String> = flow {
        val call = client.newCall(request)
        // 协程取消 → OkHttp Call 取消（结构化并发）
        coroutineContext[Job]?.invokeOnCompletion { call.cancel() }

        var response: Response? = null
        try {
            response = withContext(Dispatchers.IO) { call.execute() }
            if (response.code == 401) {
                response.close()
                val refreshed = tokenRefresher.refresh()
                if (refreshed != null) {
                    val retriedReq = request.newBuilder()
                        .header("Authorization", "Bearer $refreshed")
                        .build()
                    val retriedCall = client.newCall(retriedReq)
                    coroutineContext[Job]?.invokeOnCompletion { retriedCall.cancel() }
                    response = withContext(Dispatchers.IO) { retriedCall.execute() }
                } else {
                    sessionController.emitForceLogout()
                    throw IOException("Unauthorized")
                }
            }
            val resp = response ?: throw IOException("no response")
            if (!resp.isSuccessful) throw IOException("HTTP ${resp.code}")
            StreamProbe.mark(
                "SSE_OPEN",
                "code=${resp.code} ct=${resp.header("Content-Type") ?: "-"} ce=${resp.header("Content-Encoding") ?: "-"}",
            )
            val source = resp.body?.source() ?: throw IOException("no response body")
            while (coroutineContext.isActive) {
                val line = withContext(Dispatchers.IO) { source.readUtf8Line() } ?: break
                val trimmed = line.trim()
                if (trimmed.isEmpty() || trimmed.startsWith(":") || !trimmed.startsWith("data:")) continue
                val payload = trimmed.removePrefix("data:").trim()
                if (payload == "[DONE]") break
                StreamProbe.tickSse(payload.length)
                emit(payload)
            }
        } catch (e: kotlinx.coroutines.CancellationException) {
            StreamProbe.mark("SSE_CANCEL")
            throw e
        } catch (e: Throwable) {
            StreamProbe.mark("SSE_ERR", "${e.javaClass.simpleName}:${(e.message ?: "").take(80)}")
            throw e
        } finally {
            response?.close()
        }
    }.flowOn(Dispatchers.IO)
}

