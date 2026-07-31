import common from '@ohos.app.ability.common';
import { Prefs } from '../data/Prefs';

/**
 * 最后查看会话持久化。复刻 Android repo/LastViewedStore.kt。
 *
 * agentId → sessionId：进 Agent 时默认恢复到该会话。
 *
 * prefs 文件名：ua_last_viewed。
 */
const PREFS_NAME = 'ua_last_viewed'
const SESSIONS_KEY = 'sessions_json'

export class SessionEntry {
  agentId: string = ''
  sessionId: string = ''
}

/** SessionEntry 的 JSON 序列化形态。 */
interface SessionEntryJson {
  agentId: string
  sessionId: string
}

export class LastViewedStore {
  private prefs: Prefs

  constructor(context: common.UIAbilityContext) {
    this.prefs = new Prefs(context, PREFS_NAME)
  }

  async setSession(agentId: string, sessionId: string): Promise<void> {
    const list = await this.readSessions()
    const filtered = list.filter((e) => e.agentId !== agentId)
    const ne = new SessionEntry()
    ne.agentId = agentId
    ne.sessionId = sessionId
    filtered.push(ne)
    await this.writeSessions(filtered)
  }

  async getSession(agentId: string): Promise<string | null> {
    const list = await this.readSessions()
    for (const e of list) {
      if (e.agentId === agentId) return e.sessionId
    }
    return null
  }

  private async readSessions(): Promise<SessionEntry[]> {
    const raw = await this.prefs.getString(SESSIONS_KEY, '')
    if (raw.length === 0) return []
    try {
      const parsed = JSON.parse(raw) as Object
      if (!(parsed instanceof Array)) return []
      return parsed.filter((v: Object) => v instanceof Object).map((v: Object) => {
        const o = v as Record<string, Object>
        const e = new SessionEntry()
        if (typeof o['agentId'] === 'string') e.agentId = o['agentId']
        if (typeof o['sessionId'] === 'string') e.sessionId = o['sessionId']
        return e
      })
    } catch (e) {
      return []
    }
  }

  private async writeSessions(list: SessionEntry[]): Promise<void> {
    if (list.length === 0) {
      await this.prefs.putString(SESSIONS_KEY, null)
    } else {
      const objs: SessionEntryJson[] = list.map((e) => {
        const o: SessionEntryJson = { agentId: e.agentId, sessionId: e.sessionId }
        return o
      })
      const json = JSON.stringify(objs)
      await this.prefs.putString(SESSIONS_KEY, json)
    }
  }

}