/**
 * 纯 ArkTS 的 server_config.json 解析逻辑（无 HarmonyOS SDK 依赖）。
 * 占位符值（含 `__UA_` 子串）或解析失败时回退到 BuildConfig 兜底值。
 * 复刻 Android net/ServerConfigResolver.kt；独立成 class 便于单测。
 */

export class ServerConfigData {
  manager_url: string = ''
  gateway_url: string = ''
}

export class ServerConfigResolved {
  managerUrl: string = ''
  gatewayUrl: string = ''

  constructor(managerUrl: string, gatewayUrl: string) {
    this.managerUrl = managerUrl
    this.gatewayUrl = gatewayUrl
  }
}

export class ServerConfigResolver {
  static resolve(raw: string | null, managerDefault: string, gatewayDefault: string): ServerConfigResolved {
    const cfg = new ServerConfigData()
    if (raw !== null && raw.length > 0) {
      try {
        const parsed = JSON.parse(raw) as Record<string, Object>
        const mu = parsed['manager_url']
        const gu = parsed['gateway_url']
        if (typeof mu === 'string') cfg.manager_url = mu
        if (typeof gu === 'string') cfg.gateway_url = gu
      } catch (e) {
        // 解析失败走兜底
      }
    }
    return new ServerConfigResolved(
      ServerConfigResolver.isReal(cfg.manager_url) ? cfg.manager_url : managerDefault,
      ServerConfigResolver.isReal(cfg.gateway_url) ? cfg.gateway_url : gatewayDefault,
    )
  }

  private static isReal(v: string): boolean {
    return v.length > 0 && !v.includes('__UA_')
  }
}
