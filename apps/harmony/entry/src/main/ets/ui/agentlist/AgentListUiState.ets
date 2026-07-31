import { AccessibleAgent } from '../../net/dto/AgentDtos';

/**
 * 智能体列表 UI 状态。复刻 Android ui/agentlist/AgentListUiState.kt。
 */
export class AgentListUiState {
  loading: boolean = true
  agents: AccessibleAgent[] = []
  error: string | null = null
  searchOpen: boolean = false
  query: string = ''

  get filteredAgents(): AccessibleAgent[] {
    if (this.query.trim().length === 0) return this.agents
    const q = this.query.toLowerCase()
    return this.agents.filter((a) =>
      a.name.toLowerCase().includes(q) ||
      (a.description !== null && a.description.toLowerCase().includes(q)),
    )
  }
}
