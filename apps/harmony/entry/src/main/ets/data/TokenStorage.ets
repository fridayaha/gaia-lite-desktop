import common from '@ohos.app.ability.common';
import { Prefs } from './Prefs';

/**
 * Token 数据：access + refresh。复刻 Android net/TokenStorage.kt TokenData。
 */
export class TokenData {
  accessToken: string
  refreshToken: string

  constructor(accessToken: string, refreshToken: string) {
    this.accessToken = accessToken
    this.refreshToken = refreshToken
  }
}

/**
 * Token 存储：复刻 Android net/DataStoreTokenStorage.kt。
 * - preferences name: ua_token
 * - access_token / refresh_token 两个键
 * - ArkTS 端不暴露 Flow，ChatViewModel 用直查式（async get）
 * - refresh() 阶段 2 接入 AuthRepository 后实现；Phase 0 留空
 *
 * 用法：
 *   const storage = new TokenStorage(prefsCtx)
 *   await storage.save(new TokenData(a, r))
 *   const t = await storage.get()
 */
export class TokenStorage {
  private static readonly NAME = 'ua_token'
  private static readonly KEY_ACCESS = 'access_token'
  private static readonly KEY_REFRESH = 'refresh_token'

  private prefs: Prefs

  constructor(context: common.UIAbilityContext) {
    this.prefs = new Prefs(context, TokenStorage.NAME)
  }

  async save(token: TokenData): Promise<void> {
    await this.prefs.putString(TokenStorage.KEY_ACCESS, token.accessToken)
    await this.prefs.putString(TokenStorage.KEY_REFRESH, token.refreshToken)
  }

  async get(): Promise<TokenData | null> {
    const access = await this.prefs.getString(TokenStorage.KEY_ACCESS, '')
    const refresh = await this.prefs.getString(TokenStorage.KEY_REFRESH, '')
    if (access.length > 0 && refresh.length > 0) {
      return new TokenData(access, refresh)
    }
    return null
  }

  async getAccessToken(): Promise<string | null> {
    const v = await this.prefs.getString(TokenStorage.KEY_ACCESS, '')
    return v.length > 0 ? v : null
  }

  async getRefreshToken(): Promise<string | null> {
    const v = await this.prefs.getString(TokenStorage.KEY_REFRESH, '')
    return v.length > 0 ? v : null
  }

  async clear(): Promise<void> {
    await this.prefs.remove(TokenStorage.KEY_ACCESS)
    await this.prefs.remove(TokenStorage.KEY_REFRESH)
  }

  /**
   * Phase 0 占位：refresh() 阶段 2 接入 AuthRepository 后实现。
   * 现在 TokenRefresher 单独承担 /auth/refresh 调用。
   */
  async refresh(): Promise<string | null> {
    return null
  }
}
