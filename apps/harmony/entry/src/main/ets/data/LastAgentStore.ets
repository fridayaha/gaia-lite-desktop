import common from '@ohos.app.ability.common';
import { Prefs } from './Prefs';

/**
 * 持久化记录用户最后一次在对话界面选中的智能体。
 * 用于冷启动或从后台恢复时，默认切换到最近对话的智能体。
 * 复刻 Android repo/LastAgentStore.kt：
 * - preferences name: ua_last_agent
 * - last_agent_id / last_agent_engine_type 两个键
 * - agentId 为空 → 清空两个键；engineType 为空 → 不写 engineTypeKey
 */
export class LastAgent {
  agentId: string
  engineType: string | null

  constructor(agentId: string, engineType: string | null) {
    this.agentId = agentId
    this.engineType = engineType
  }
}

export class LastAgentStore {
  private static readonly NAME = 'ua_last_agent'
  private static readonly KEY_AGENT_ID = 'last_agent_id'
  private static readonly KEY_ENGINE_TYPE = 'last_agent_engine_type'

  private prefs: Prefs

  constructor(context: common.UIAbilityContext) {
    this.prefs = new Prefs(context, LastAgentStore.NAME)
  }

  async get(): Promise<LastAgent | null> {
    const agentId = await this.prefs.getString(LastAgentStore.KEY_AGENT_ID, '')
    if (agentId.length === 0) return null
    const engineType = await this.prefs.getString(LastAgentStore.KEY_ENGINE_TYPE, '')
    return new LastAgent(agentId, engineType.length > 0 ? engineType : null)
  }

  async set(agentId: string | null, engineType: string | null): Promise<void> {
    if (agentId === null || agentId.length === 0) {
      await this.prefs.remove(LastAgentStore.KEY_AGENT_ID)
      await this.prefs.remove(LastAgentStore.KEY_ENGINE_TYPE)
      return
    }
    await this.prefs.putString(LastAgentStore.KEY_AGENT_ID, agentId)
    if (engineType === null || engineType.length === 0) {
      await this.prefs.remove(LastAgentStore.KEY_ENGINE_TYPE)
    } else {
      await this.prefs.putString(LastAgentStore.KEY_ENGINE_TYPE, engineType)
    }
  }

  async clear(): Promise<void> {
    await this.set(null, null)
  }
}
