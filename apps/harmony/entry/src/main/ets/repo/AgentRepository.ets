import { ManagerApi } from '../net/ManagerApi';
import { AccessibleAgent, AgentDeploymentStatus, AgentModelsResponse } from '../net/dto/AgentDtos';

/**
 * 智能体仓库。复刻 Android repo/AgentRepository.kt。
 */
export class AgentRepository {
  private managerApi: ManagerApi

  constructor(managerApi: ManagerApi) {
    this.managerApi = managerApi
  }

  async getAccessibleAgents(): Promise<AccessibleAgent[]> {
    return await this.managerApi.getAccessibleAgents()
  }

  async getDeploymentStatus(agentId: string): Promise<AgentDeploymentStatus> {
    return await this.managerApi.getDeploymentStatus(agentId)
  }

  async deploy(agentId: string): Promise<AgentDeploymentStatus> {
    return await this.managerApi.deploy(agentId)
  }

  async getModels(agentId: string): Promise<AgentModelsResponse> {
    return await this.managerApi.getModels(agentId)
  }
}
