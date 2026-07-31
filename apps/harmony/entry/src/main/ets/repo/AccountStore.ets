import common from '@ohos.app.ability.common';
import { Prefs } from '../data/Prefs';
import { UserInfo } from '../net/dto/AuthDtos';
import { TokenData } from '../data/TokenStorage';

/**
 * 多账号持久化。复刻 Android repo/AccountStore.kt。
 * prefs 文件名：ua_account_store；key: saved_accounts_json。
 * 存储 JSON 字符串数组，每条 SavedAccount 含 id/username/email/avatarUrl/access/refresh/savedAt。
 */
const PREFS_NAME = 'ua_account_store'
const KEY = 'saved_accounts_json'

export class SavedAccount {
  id: string = ''
  username: string = ''
  email: string | null = null
  avatar_url: string | null = null
  access_token: string = ''
  refresh_token: string = ''
  saved_at: number = 0

  static fromJson(o: Record<string, Object>): SavedAccount {
    const r = new SavedAccount()
    if (typeof o['id'] === 'string') r.id = o['id']
    if (typeof o['username'] === 'string') r.username = o['username']
    if (typeof o['email'] === 'string') r.email = o['email']
    if (typeof o['avatar_url'] === 'string') r.avatar_url = o['avatar_url']
    if (typeof o['access_token'] === 'string') r.access_token = o['access_token']
    if (typeof o['refresh_token'] === 'string') r.refresh_token = o['refresh_token']
    if (typeof o['saved_at'] === 'number') r.saved_at = o['saved_at']
    return r
  }

  toJson(): Record<string, Object> {
    const o: Record<string, Object> = {}
    o['id'] = this.id
    o['username'] = this.username
    o['access_token'] = this.access_token
    o['refresh_token'] = this.refresh_token
    o['saved_at'] = this.saved_at
    if (this.email !== null) o['email'] = this.email
    if (this.avatar_url !== null) o['avatar_url'] = this.avatar_url
    return o
  }
}

export class AccountStore {
  private prefs: Prefs
  private cached: SavedAccount[] = []
  private listeners: Array<(accounts: SavedAccount[]) => void> = []

  constructor(context: common.UIAbilityContext) {
    this.prefs = new Prefs(context, PREFS_NAME)
  }

  /** 订阅账号列表变化。立即触发一次当前值。 */
  subscribe(listener: (accounts: SavedAccount[]) => void): void {
    this.listeners.push(listener)
    listener(this.cached)
  }

  unsubscribe(listener: (accounts: SavedAccount[]) => void): void {
    this.listeners = this.listeners.filter((l) => l !== listener)
  }

  async restore(): Promise<void> {
    this.cached = await this.listAccounts()
    this.notify(this.cached)
  }

  async listAccounts(): Promise<SavedAccount[]> {
    const raw = await this.prefs.getString(KEY, '')
    if (raw.length === 0) return []
    try {
      const parsed = JSON.parse(raw) as Object
      if (!(parsed instanceof Array)) return []
      return parsed
        .filter((v: Object) => v instanceof Object)
        .map((v: Object) => SavedAccount.fromJson(v as Record<string, Object>))
    } catch (e) {
      return []
    }
  }

  async saveAccount(user: UserInfo, token: TokenData): Promise<void> {
    if (user.id === null) return
    const accounts = await this.listAccounts()
    const filtered = accounts.filter((a) => a.id !== user.id)
    const acc = new SavedAccount()
    acc.id = user.id
    acc.username = user.username
    acc.email = user.email
    acc.avatar_url = user.avatar_url
    acc.access_token = token.accessToken
    acc.refresh_token = token.refreshToken
    acc.saved_at = Date.now()
    const updated = filtered.concat([acc])
    // 按保存时间倒序
    updated.sort((a, b) => b.saved_at - a.saved_at)
    await this.writeAll(updated)
    this.cached = updated
    this.notify(updated)
  }

  async removeAccount(userId: string): Promise<void> {
    const accounts = (await this.listAccounts()).filter((a) => a.id !== userId)
    await this.writeAll(accounts)
    this.cached = accounts
    this.notify(accounts)
  }

  async getAccount(userId: string): Promise<SavedAccount | null> {
    const list = await this.listAccounts()
    for (const a of list) {
      if (a.id === userId) return a
    }
    return null
  }

  private async writeAll(accounts: SavedAccount[]): Promise<void> {
    if (accounts.length === 0) {
      await this.prefs.putString(KEY, null)
    } else {
      const json = JSON.stringify(accounts.map((a) => a.toJson()))
      await this.prefs.putString(KEY, json)
    }
  }

  private notify(accounts: SavedAccount[]): void {
    for (const l of this.listeners) {
      try { l(accounts) } catch (e) {}
    }
  }
}
