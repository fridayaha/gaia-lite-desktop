package com.unionagents.enduser.repo

import com.unionagents.enduser.net.ManagerApi
import com.unionagents.enduser.net.dto.AccessibleAgent
import com.unionagents.enduser.net.dto.AgentDeploymentStatus
import com.unionagents.enduser.net.dto.AgentModelsResponse
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class AgentRepository @Inject constructor(
    private val managerApi: ManagerApi,
) {
    suspend fun getAccessibleAgents(): List<AccessibleAgent> =
        managerApi.getAccessibleAgents()

    suspend fun getDeploymentStatus(agentId: String): AgentDeploymentStatus =
        managerApi.getDeploymentStatus(agentId)

    suspend fun deploy(agentId: String): AgentDeploymentStatus =
        managerApi.deploy(agentId)

    suspend fun getModels(agentId: String): AgentModelsResponse =
        managerApi.getModels(agentId)
}
