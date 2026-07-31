import { TokenStorage } from '../data/TokenStorage';
import { parseExp } from '../util/JwtUtil';
import { logI, logW } from '../util/Logger';
import { TokenRefresherLike } from './TokenRefresher';

/**
 * 会话生命周期控制 —— app 启动时主动检查 access_token 是否快过期，过期则提前 refresh；
 * refresh 失败（refresh_token 也过期）时发 forceLogout 事件，UI 收到后跳登录页。
 *
 * 三处 forceLogout 触发点（与 Android 对齐）：
 *  - SessionController.ensureSession：app 启动时 access 快过期且 refresh 失败
 *  - HttpClient.request：普通请求 401 → refresh 失败
 *  - SseClient.stream：SSE 路径 401 → refresh 失败（SSE 不经过 Authenticator）
 *
 * ArkTS 无 SharedFlow，用 callback 列表实现；forceLogout 只触发一次（一次性 flag）。
 */
export class SessionController {
  /** 距过期 <30s 触发主动 refresh */
  private static readonly EXPIRY_THRESHOLD_SECONDS = 30

  private forceLogoutListeners: Array<() => void> = []
  private forceLogoutFired = false
  private tokenStorage: TokenStorage
  private tokenRefresher: TokenRefresherLike

  constructor(
    tokenStorage: TokenStorage,
    tokenRefresher: TokenRefresherLike,
  ) {
    this.tokenStorage = tokenStorage
    this.tokenRefresher = tokenRefresher
  }

  onForceLogout(listener: () => void): void {
    this.forceLogoutListeners.push(listener)
  }

  emitForceLogout(): void {
    if (this.forceLogoutFired) return
    this.forceLogoutFired = true
    logW('SessionController', 'forceLogout emitted')
    for (const l of this.forceLogoutListeners) {
      try {
        l()
      } catch (e) {
        // 听众异常不影响其他
      }
    }
  }

  /**
   * App 启动时调用。access_token 距过期 <30s 则主动 refresh，失败则发 forceLogout。
   * access_token 解析失败（不是 JWT 或无 exp）→ 不处理，交给 401 兜底。
   */
  async ensureSession(): Promise<void> {
    const access = await this.tokenStorage.getAccessToken()
    if (access === null) return
    const exp = parseExp(access)
    if (exp === null) return
    const nowSec = Math.floor(Date.now() / 1000)
    if (exp - nowSec < SessionController.EXPIRY_THRESHOLD_SECONDS) {
      logI('SessionController', `access expiring in ${exp - nowSec}s; refreshing`)
      const refreshed = await this.tokenRefresher.refresh()
      if (refreshed === null) this.emitForceLogout()
    }
  }
}
