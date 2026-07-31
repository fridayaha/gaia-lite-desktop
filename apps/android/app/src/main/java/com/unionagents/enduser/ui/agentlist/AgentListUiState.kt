package com.unionagents.enduser.ui.agentlist

import com.unionagents.enduser.net.dto.AccessibleAgent

data class AgentListUiState(
    val loading: Boolean = true,
    val agents: List<AccessibleAgent> = emptyList(),
    val error: String? = null,
    val searchOpen: Boolean = false,
    val query: String = "",
) {
    val filteredAgents: List<AccessibleAgent>
        get() = if (query.isBlank()) agents else agents.filter { agent ->
            agent.name.contains(query, ignoreCase = true) ||
                (agent.description?.contains(query, ignoreCase = true) == true)
        }
}

