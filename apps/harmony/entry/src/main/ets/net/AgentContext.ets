import { LastAgentStore, LastAgent } from '../data/LastAgentStore';

/**
 * 当前会话上下文：agentId / sessionId / engineType。
 * 复刻 Android net/AgentContext.kt：
 * - 由 ChatViewModel 在进入/切换会话时写入
 * - HttpClient 读取后注入 X-Agent-ID 等头
 * - 把「最后一次选中的智能体」持久化到 LastAgentStore
 *
 * ArkTS 无 Flow / CoroutineScope，用 callback + 显式 persist() 调用。
 * 冷启动时由 AppContainer 显式调 restoreFromStore() 一次性恢复。
 */
export class AgentContext {
  /** 不可变状态对象，每次 setAgent/setSession/setEngineType 产生新实例。 */
  private _state: State = new State()
  private lastAgentStore: LastAgentStore

  constructor(lastAgentStore: LastAgentStore) {
    this.lastAgentStore = lastAgentStore
  }

  get state(): State {
    return this._state
  }

  /**
   * 冷启动恢复：从 LastAgentStore 读 last agent。若已有 agentId 则不覆盖。
   * 由 AppContainer 在 EntryAbility.onCreate 后调用。
   */
  async restoreFromStore(): Promise<void> {
    const saved: LastAgent | null = await this.lastAgentStore.get()
    if (saved !== null && this._state.agentId === null) {
      this._state = new State(saved.agentId, this._state.sessionId, saved.engineType)
    }
  }

  setAgent(agentId: string, engineType: string | null = null): void {
    this._state = new State(agentId, null, engineType)
    // 异步持久化，不阻塞 UI
    this.lastAgentStore.set(agentId, engineType).catch(() => {})
  }

  setSession(sessionId: string | null): void {
    this._state = new State(this._state.agentId, sessionId, this._state.engineType)
  }

  setEngineType(engineType: string | null): void {
    this._state = new State(this._state.agentId, this._state.sessionId, engineType)
    if (this._state.agentId !== null) {
      this.lastAgentStore.set(this._state.agentId, engineType).catch(() => {})
    }
  }

  async clear(): Promise<void> {
    this._state = new State()
    await this.lastAgentStore.clear()
  }
}

export class State {
  agentId: string | null = null
  sessionId: string | null = null
  engineType: string | null = null

  constructor(agentId: string | null = null, sessionId: string | null = null, engineType: string | null = null) {
    this.agentId = agentId
    this.sessionId = sessionId
    this.engineType = engineType
  }
}
