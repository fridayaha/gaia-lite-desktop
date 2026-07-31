import dataPreferences from '@ohos.data.preferences';
import common from '@ohos.app.ability.common';
import { BusinessError } from '@ohos.base';

/**
 * Preferences 包装：对应 Android DataStore Preferences 的 4 个文件（ua_token / ua_last_agent /
 * ua_account_store / ua_developer_mode）。HarmonyOS preferences API 与 DataStore 形态接近：
 * - getPreferences(context, name) 拿到 Preferences 实例
 * - put / get / delete / flush
 * ArkTS preferences 全部异步，DataStore 的 tokenFlow 在 ArkTS 端不需要——ChatViewModel 用直查式。
 */
export class Prefs {
  private context: common.UIAbilityContext
  private name: string

  constructor(context: common.UIAbilityContext, name: string) {
    this.context = context
    this.name = name
  }

  private async pref(): Promise<dataPreferences.Preferences> {
    return await dataPreferences.getPreferences(this.context, this.name)
  }

  async getString(key: string, def: string = ''): Promise<string> {
    const p = await this.pref()
    const v = await p.get(key, def)
    return typeof v === 'string' ? v : ''
  }

  async getBoolean(key: string, def: boolean = false): Promise<boolean> {
    const p = await this.pref()
    const v = await p.get(key, def)
    return typeof v === 'boolean' ? v : def
  }

  async putString(key: string, value: string | null): Promise<void> {
    const p = await this.pref()
    if (value === null || value === undefined) {
      await p.delete(key)
    } else {
      await p.put(key, value)
    }
    await p.flush()
  }

  async putBoolean(key: string, value: boolean): Promise<void> {
    const p = await this.pref()
    await p.put(key, value)
    await p.flush()
  }

  async remove(key: string): Promise<void> {
    const p = await this.pref()
    await p.delete(key)
    await p.flush()
  }
}
