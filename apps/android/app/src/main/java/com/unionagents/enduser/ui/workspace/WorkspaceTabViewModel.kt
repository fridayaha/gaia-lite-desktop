package com.unionagents.enduser.ui.workspace

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.unionagents.enduser.net.AgentContext
import com.unionagents.enduser.net.dto.AccessibleAgent
import com.unionagents.enduser.repo.AgentRepository
import com.unionagents.enduser.repo.DeveloperModeStore
import com.unionagents.enduser.repo.LastAgentStore
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class WorkspaceTabViewModel @Inject constructor(
    private val agentRepository: AgentRepository,
    private val developerModeStore: DeveloperModeStore,
    private val agentContext: AgentContext,
    private val lastAgentStore: LastAgentStore,
) : ViewModel() {

    data class UiState(
        val loading: Boolean = true,
        val error: String? = null,
        val agents: List<AccessibleAgent> = emptyList(),
        val selectedAgentId: String? = null,
        val developerMode: Boolean = false,
    )

    private val _ui = MutableStateFlow(UiState())
    val ui: StateFlow<UiState> = _ui.asStateFlow()

    init {
        load()
        viewModelScope.launch {
            developerModeStore.flow.collect { enabled ->
                _ui.update { it.copy(developerMode = enabled) }
            }
        }
        viewModelScope.launch {
            agentContext.state
                .map { it.agentId }
                .distinctUntilChanged()
                .collect { agentId ->
                    if (agentId == null) return@collect
                    val agents = _ui.value.agents
                    if (agents.any { it.id == agentId } && _ui.value.selectedAgentId != agentId) {
                        _ui.update { it.copy(selectedAgentId = agentId) }
                        updateAgentContext(agentId, agents)
                    }
                }
        }
    }

    fun load() {
        _ui.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            try {
                val agents = agentRepository.getAccessibleAgents()
                val preferredId = agentContext.state.value.agentId
                    ?: lastAgentStore.get()?.agentId
                val selected = resolveSelectedAgent(agents, preferredId)
                _ui.update {
                    it.copy(
                        loading = false,
                        agents = agents,
                        selectedAgentId = selected,
                    )
                }
                selected?.let { updateAgentContext(it, agents) }
            } catch (e: Throwable) {
                _ui.update { it.copy(loading = false, error = e.message ?: "加载失败") }
            }
        }
    }

    fun selectAgent(agentId: String) {
        _ui.update { it.copy(selectedAgentId = agentId) }
        updateAgentContext(agentId, _ui.value.agents)
    }

    private fun updateAgentContext(agentId: String, agents: List<AccessibleAgent>) {
        val engineType = agents.find { it.id == agentId }?.engineType
        agentContext.setAgent(agentId, engineType)
    }

    private fun resolveSelectedAgent(agents: List<AccessibleAgent>, preferredId: String? = null): String? {
        if (agents.isEmpty()) return null
        val candidate = preferredId ?: agentContext.state.value.agentId
        if (candidate != null && agents.any { it.id == candidate }) {
            return candidate
        }
        return agents.maxByOrNull { it.lastAccessedAt ?: "" }?.id ?: agents.first().id
    }
}
