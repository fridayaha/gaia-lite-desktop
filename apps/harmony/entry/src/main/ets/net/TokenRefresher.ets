import { TokenStorage, TokenData } from '../data/TokenStorage';

/** refresh 阶段产出的新 token 对。 */
export interface RefreshResult {
  access: string
  refresh: string | null
}

/** refreshExecutor 函数签名：给一个旧 refreshToken，返回 RefreshResult 或 null。 */
export type RefreshExecutor = (refreshToken: string) => Promise<RefreshResult | null>;

/**
 * TokenRefresher 形态接口。SessionController 仅依赖此接口而非具体类，
 * 避免循环依赖（TokenRefresher ↔ HttpClient）。
 */
export interface TokenRefresherLike {
  refresh(): Promise<string | null>
}

/**
 * Token refresh 单飞工具 —— 给 HttpClient（普通请求 401）和 SseClient（SSE 401）共用。
 * 复刻 Android net/TokenRefresher.kt：
 * - Mutex 单飞：并发 401 合并成一次 refresh
 * - refresh 走专用无鉴权路径（不经过 401 重试，避免循环）
 * - 失败返回 null；调用方自行处理（forceLogout / retry 取消）
 *
 * ArkTS 无 Mutex，用 inFlightPromise 串行化。
 */
export class TokenRefresher implements TokenRefresherLike {
  private inFlight: Promise<string | null> | null = null
  private tokenStorage: TokenStorage
  private refreshExecutor: RefreshExecutor

  constructor(
    tokenStorage: TokenStorage,
    refreshExecutor: RefreshExecutor,
  ) {
    this.tokenStorage = tokenStorage
    this.refreshExecutor = refreshExecutor
  }

  /**
   * 同步刷新一次 access token；返回新 access token，失败返回 null。
   * 调用方拿到新 token 后自己 retry 原请求。
   */
  async refresh(): Promise<string | null> {
    if (this.inFlight !== null) {
      return await this.inFlight
    }
    const p = this.doRefresh()
    this.inFlight = p
    try {
      return await p
    } finally {
      this.inFlight = null
    }
  }

  private async doRefresh(): Promise<string | null> {
    const prevRefresh = await this.tokenStorage.getRefreshToken()
    if (prevRefresh === null) return null
    const result = await this.refreshExecutor(prevRefresh)
    if (result === null) return null
    await this.tokenStorage.save(new TokenData(result.access, result.refresh ?? prevRefresh))
    return result.access
  }
}
