package com.unionagents.enduser.ui.agentlist

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.unionagents.enduser.net.AgentContext
import com.unionagents.enduser.repo.AgentRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class AgentListViewModel @Inject constructor(
    private val agentRepository: AgentRepository,
    private val agentContext: AgentContext,
) : ViewModel() {

    private val _ui = MutableStateFlow(AgentListUiState())
    val ui: StateFlow<AgentListUiState> = _ui.asStateFlow()

    init {
        load()
    }

    fun load() {
        _ui.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            try {
                val agents = agentRepository.getAccessibleAgents()
                _ui.update { it.copy(loading = false, agents = agents) }
            } catch (e: Throwable) {
                _ui.update { it.copy(loading = false, error = e.message ?: "加载失败") }
            }
        }
    }

    fun selectAgent(agentId: String, engineType: String?) {
        agentContext.setAgent(agentId, engineType)
    }

    fun openSearch() {
        _ui.update { it.copy(searchOpen = true) }
    }

    fun closeSearch() {
        _ui.update { it.copy(searchOpen = false, query = "") }
    }

    fun setQuery(text: String) {
        _ui.update { it.copy(query = text) }
    }
}
