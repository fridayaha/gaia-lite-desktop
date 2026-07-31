package com.unionagents.enduser.net

import okhttp3.Interceptor
import okhttp3.Request
import okhttp3.Response
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 给 Gateway 请求附加 X-Agent-ID / X-Engine-Type / X-Session-ID 头。
 * 仅注册到 @GatewayClient —— Manager 请求的 agentId 在 path 里，不需要头。
 * 镜像 apps/enduser/src/composables/useChat.ts 的 gwHeaders() 行为。
 *
 * X-Client-Type 无条件携带：gateway 用它把 Langfuse trace 的 channel_type 记为 android
 * （缺省记 web），与 agentId 是否已选定无关。
 */
@Singleton
class AgentHeaderInterceptor @Inject constructor(
    private val agentContext: AgentContext,
) : Interceptor {

    override fun intercept(chain: Interceptor.Chain): Response =
        chain.proceed(buildAgentRequest(chain.request(), agentContext.state.value))
}

internal fun buildAgentRequest(req: Request, state: AgentContext.State): Request {
    val builder = req.newBuilder().header("X-Client-Type", "android")
    state.agentId?.let { builder.header("X-Agent-ID", it) }
    state.engineType?.let { builder.header("X-Engine-Type", it) }
    state.sessionId?.let { builder.header("X-Session-ID", it) }
    return builder.build()
}
