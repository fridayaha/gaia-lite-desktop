package com.unionagents.enduser.net

import com.unionagents.enduser.repo.LastAgentStore
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 当前会话上下文：agentId / sessionId / engineType。
 * 由 ChatViewModel 在进入/切换会话时写入，AgentHeaderInterceptor 读取后注入 X-Agent-ID 等头。
 * 镜像 apps/enduser/src/composables/useChat.ts 的 gwHeaders(agentId, { sessionId, engineType }) 模式，
 * 但用全局单例避免每个请求都显式传参。
 *
 * 同时负责把「最后一次选中的智能体」持久化到 [LastAgentStore]，使用应用级 CoroutineScope，
 * 避免绑定 ViewModel 生命周期导致用户快速退出时写入被取消。
 */
@Singleton
class AgentContext @Inject constructor(
    private val lastAgentStore: LastAgentStore,
) {
    data class State(
        val agentId: String? = null,
        val sessionId: String? = null,
        val engineType: String? = null,
    )

    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state.asStateFlow()

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    init {
        // 冷启动时从 DataStore 恢复最近使用的智能体；若启动后其他模块已设置 agentId 则保留当前值。
        scope.launch {
            val saved = lastAgentStore.get()
            if (saved != null) {
                _state.update { current ->
                    if (current.agentId == null) {
                        current.copy(agentId = saved.agentId, engineType = saved.engineType ?: current.engineType)
                    } else {
                        current
                    }
                }
            }
        }
    }

    fun setAgent(agentId: String, engineType: String? = null) {
        _state.update { State(agentId = agentId, engineType = engineType) }
        scope.launch {
            lastAgentStore.set(agentId, engineType)
        }
    }

    fun setSession(sessionId: String?) {
        _state.update { it.copy(sessionId = sessionId) }
    }

    fun setEngineType(engineType: String?) {
        _state.update { it.copy(engineType = engineType) }
    }

    fun clear() {
        _state.value = State()
        scope.launch {
            lastAgentStore.clear()
        }
    }
}
