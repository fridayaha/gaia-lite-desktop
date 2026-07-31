import { AgentRepository } from '../../repo/AgentRepository';
import { AgentContext } from '../../net/AgentContext';
import { LastAgentStore } from '../../data/LastAgentStore';
import { AccessibleAgent } from '../../net/dto/AgentDtos';
import { logE } from '../../util/Logger';

/**
 * 工作区 Tab 顶层 ViewModel。复刻 Android ui/workspace/WorkspaceTabViewModel.kt。
 *
 * 负责：拉取 accessible agents 列表 → 默认选中 lastAgent 或最近使用 →
 * 把选中 agent 写入 AgentContext；切换 agent 时同步 AgentContext。
 *
 * Phase 1 子集：不订阅 developerModeStore（Phase 2）；不订阅 agentContext.state.agentId 流。
 */
export class WorkspaceTabUiState {
  loading: boolean = true
  error: string | null = null
  agents: AccessibleAgent[] = []
  selectedAgentId: string | null = null
  developerMode: boolean = false
}

export class WorkspaceTabViewModel {
  private _state: WorkspaceTabUiState = new WorkspaceTabUiState()
  private listeners: Array<(s: WorkspaceTabUiState) => void> = []
  private agentRepo: AgentRepository
  private agentContext: AgentContext
  private lastAgentStore: LastAgentStore

  constructor(
    agentRepo: AgentRepository,
    agentContext: AgentContext,
    lastAgentStore: LastAgentStore,
  ) {
    this.agentRepo = agentRepo
    this.agentContext = agentContext
    this.lastAgentStore = lastAgentStore
  }

  get state(): WorkspaceTabUiState {
    return this._state
  }

  subscribe(listener: (s: WorkspaceTabUiState) => void): void {
    this.listeners.push(listener)
    listener(this._state)
  }

  unsubscribe(listener: (s: WorkspaceTabUiState) => void): void {
    this.listeners = this.listeners.filter((l) => l !== listener)
  }

  private setState(patch: (s: WorkspaceTabUiState) => void): void {
    const next = new WorkspaceTabUiState()
    next.loading = this._state.loading
    next.error = this._state.error
    next.agents = this._state.agents.slice()
    next.selectedAgentId = this._state.selectedAgentId
    next.developerMode = this._state.developerMode
    patch(next)
    this._state = next
    for (const l of this.listeners) {
      try { l(this._state) } catch (e) { logE('WorkspaceTabViewModel', `notify failed: ${e}`) }
    }
  }

  async load(): Promise<void> {
    this.setState((s) => { s.loading = true; s.error = null })
    try {
      const agents = await this.agentRepo.getAccessibleAgents()
      const preferredId = this.agentContext.state.agentId ?? (await this.lastAgentStore.get())?.agentId ?? null
      const selected = this.resolveSelectedAgent(agents, preferredId)
      this.setState((s) => {
        s.loading = false
        s.agents = agents
        s.selectedAgentId = selected
      })
      if (selected !== null) this.updateAgentContext(selected, agents)
    } catch (e) {
      const msg = e instanceof Error ? (e.message || '加载失败') : '加载失败'
      this.setState((s) => { s.loading = false; s.error = msg })
    }
  }

  selectAgent(agentId: string): void {
    this.setState((s) => { s.selectedAgentId = agentId })
    this.updateAgentContext(agentId, this._state.agents)
  }

  private updateAgentContext(agentId: string, agents: AccessibleAgent[]): void {
    const agent = agents.find((a) => a.id === agentId)
    this.agentContext.setAgent(agentId, agent?.engine_type ?? null)
  }

  private resolveSelectedAgent(agents: AccessibleAgent[], preferredId: string | null): string | null {
    if (agents.length === 0) return null
    const candidate = preferredId ?? this.agentContext.state.agentId
    if (candidate !== null && agents.some((a) => a.id === candidate)) return candidate
    // 选中 lastAccessedAt 最大者；都没有则取第一个
    let best: AccessibleAgent = agents[0]
    let bestAt = best.last_accessed_at ?? ''
    for (let i = 1; i < agents.length; i++) {
      const at = agents[i].last_accessed_at ?? ''
      if (at > bestAt) {
        best = agents[i]
        bestAt = at
      }
    }
    return best.id
  }
}
