import { AgentRepository } from '../../repo/AgentRepository';
import { AgentContext } from '../../net/AgentContext';
import { AgentListUiState } from './AgentListUiState';

/**
 * 智能体列表 ViewModel。复刻 Android ui/agentlist/AgentListViewModel.kt。
 */
export class AgentListViewModel {
  private _state: AgentListUiState = new AgentListUiState()
  private listeners: Array<(s: AgentListUiState) => void> = []
  private agentRepository: AgentRepository
  private agentContext: AgentContext

  constructor(
    agentRepository: AgentRepository,
    agentContext: AgentContext,
  ) {
    this.agentRepository = agentRepository
    this.agentContext = agentContext
  }

  get state(): AgentListUiState {
    return this._state
  }

  subscribe(listener: (s: AgentListUiState) => void): void {
    this.listeners.push(listener)
    listener(this._state)
  }

  unsubscribe(listener: (s: AgentListUiState) => void): void {
    this.listeners = this.listeners.filter((l) => l !== listener)
  }

  private setState(patch: (s: AgentListUiState) => void): void {
    const next = new AgentListUiState()
    next.loading = this._state.loading
    next.agents = this._state.agents
    next.error = this._state.error
    next.searchOpen = this._state.searchOpen
    next.query = this._state.query
    patch(next)
    this._state = next
    for (const l of this.listeners) {
      try { l(this._state) } catch (e) {}
    }
  }

  async load(): Promise<void> {
    this.setState((s) => { s.loading = true; s.error = null })
    try {
      const agents = await this.agentRepository.getAccessibleAgents()
      this.setState((s) => { s.loading = false; s.agents = agents })
    } catch (e) {
      const msg = e instanceof Error ? e.message : '加载失败'
      this.setState((s) => { s.loading = false; s.error = msg })
    }
  }

  selectAgent(agentId: string, engineType: string | null): void {
    this.agentContext.setAgent(agentId, engineType)
  }

  openSearch(): void {
    this.setState((s) => { s.searchOpen = true })
  }

  closeSearch(): void {
    this.setState((s) => { s.searchOpen = false; s.query = '' })
  }

  setQuery(text: string): void {
    this.setState((s) => { s.query = text })
  }
}
