package com.unionagents.enduser.repo

import javax.inject.Inject
import javax.inject.Singleton

/**
 * 模型列表：优先走 manager（/agent-instances/{id}/models），fallback 走 gateway 引擎 /v1/models。
 * 镜像 apps/enduser/src/composables/useChat.ts 的 loadModels() 双源策略。
 */
@Singleton
class ModelRepository @Inject constructor(
    private val agentRepository: AgentRepository,
    private val gatewayApi: com.unionagents.enduser.net.GatewayApi,
) {
    suspend fun getModels(agentId: String): List<String> {
        try {
            val resp = agentRepository.getModels(agentId)
            if (resp.data.isNotEmpty()) return resp.data.map { it.id }
        } catch (_: Throwable) {
            // 继续走 fallback
        }
        return try {
            gatewayApi.getEngineModels().data.map { it.id }
        } catch (_: Throwable) {
            emptyList()
        }
    }
}
