import common from '@ohos.app.ability.common';
import buffer from '@ohos.buffer';
import { ServerConfigResolver, ServerConfigResolved } from './ServerConfigResolver';

/**
 * HttpClient 端所需 serverConfig 形态。ServerConfig 类实现此接口，
 * 避免严格 ArkTS 不允许结构化类型带来的赋值失败。
 */
export interface ServerConfigLike {
  managerUrl: string
  gatewayUrl: string
}

/**
 * 运行期后端地址读取器。复刻 Android net/ServerConfig.kt。
 *
 * 优先读 hap 内 `resources/base/rawfile/server_config.json`——该文件在 patch 阶段被替换成
 * 当前 ECS 的真实 URL（占位符 `__UA_MANAGER_URL__` / `__UA_GATEWAY_URL__` 被替换）。
 *
 * rawfile 仍是占位符（未 patch）或读取失败时，回退到默认值——保证未 patch 的 debug hap 也能跑通。
 *
 * 单例 + 缓存：rawfile 编译进 hap 后运行期不变，进程内只读一次。
 */
export class ServerConfig implements ServerConfigLike {
  /**
   * 默认值——指向 ECS 公网入口（端口 30082 由 console-landing nginx 反代到内部 manager/gateway）。
   * 与 Android app/build.gradle.kts 的 BuildConfig.MANAGER_BASE_URL/GATEWAY_BASE_URL 一致。
   * rawfile/server_config.json 走 patch 流程替换占位符后覆盖此值。
   */
  private static readonly DEFAULT_MANAGER = 'http://190.92.230.115:30082/api/manager/'
  private static readonly DEFAULT_GATEWAY = 'http://190.92.230.115:30082/api/gateway/'

  private cached: ServerConfigResolved | null = null
  private context: common.UIAbilityContext

  constructor(context: common.UIAbilityContext) {
    this.context = context
  }

  get managerUrl(): string {
    return this.load().managerUrl
  }

  get gatewayUrl(): string {
    return this.load().gatewayUrl
  }

  private load(): ServerConfigResolved {
    if (this.cached !== null) return this.cached
    let raw: string | null = null
    try {
      const buf = this.context.resourceManager.getRawFileContentSync('server_config.json')
      raw = buffer.from(buf).toString('utf-8')
    } catch (e) {
      raw = null
    }
    const resolved = ServerConfigResolver.resolve(
      raw,
      ServerConfig.DEFAULT_MANAGER,
      ServerConfig.DEFAULT_GATEWAY,
    )
    this.cached = resolved
    return resolved
  }
}
