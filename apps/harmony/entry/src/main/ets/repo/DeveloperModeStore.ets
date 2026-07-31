import common from '@ohos.app.ability.common';
import { Prefs } from '../data/Prefs';

/**
 * 开发者模式开关。复刻 Android repo/DeveloperModeStore.kt。
 * 默认关闭；打开后工作区根目录展示 Hermes 内部黑名单目录。
 * prefs 文件名：ua_developer_mode。
 */
const PREFS_NAME = 'ua_developer_mode'
const KEY = 'developer_mode'

export class DeveloperModeStore {
  private prefs: Prefs
  private cached: boolean = false
  private listeners: Array<(enabled: boolean) => void> = []

  constructor(context: common.UIAbilityContext) {
    this.prefs = new Prefs(context, PREFS_NAME)
  }

  /** 订阅开关变化。立即触发一次当前值。 */
  subscribe(listener: (enabled: boolean) => void): void {
    this.listeners.push(listener)
    listener(this.cached)
  }

  unsubscribe(listener: (enabled: boolean) => void): void {
    this.listeners = this.listeners.filter((l) => l !== listener)
  }

  /** 从 prefs 读取当前值并缓存。AppContainer.onLaunch 调一次。 */
  async restore(): Promise<void> {
    this.cached = await this.prefs.getBoolean(KEY, false)
    this.notify(this.cached)
  }

  get current(): boolean {
    return this.cached
  }

  async set(enabled: boolean): Promise<void> {
    await this.prefs.putBoolean(KEY, enabled)
    this.cached = enabled
    this.notify(enabled)
  }

  private notify(enabled: boolean): void {
    for (const l of this.listeners) {
      try { l(enabled) } catch (e) {}
    }
  }
}
