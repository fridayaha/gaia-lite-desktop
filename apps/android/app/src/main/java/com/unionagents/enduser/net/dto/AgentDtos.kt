package com.unionagents.enduser.net.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class AccessibleAgent(
    val id: String,
    val name: String,
    val description: String? = null,
    @SerialName("engine_type") val engineType: String? = null,
    @SerialName("last_accessed_at") val lastAccessedAt: String? = null,
)

@Serializable
data class AgentDeploymentStatus(
    @SerialName("agent_id") val agentId: String,
    val status: String,
    @SerialName("engine_url") val engineUrl: String? = null,
    @SerialName("last_active_at") val lastActiveAt: String? = null,
    @SerialName("error_message") val errorMessage: String? = null,
    @SerialName("pod_name") val podName: String? = null,
    @SerialName("pod_start_time") val podStartTime: String? = null,
    @SerialName("pod_phase") val podPhase: String? = null,
)

@Serializable
data class AgentModelItem(
    val id: String,
    val `object`: String? = null,
    val provider: String? = null,
)

@Serializable
data class AgentModelsResponse(
    val `object`: String? = null,
    val data: List<AgentModelItem> = emptyList(),
)
