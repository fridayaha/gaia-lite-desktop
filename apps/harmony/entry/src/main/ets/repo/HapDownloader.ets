import common from '@ohos.app.ability.common';

/**
 * .hap 下载 + 安装 stub。
 * Phase 3 才用到；HarmonyOS 6.1 的 @ohos.request.agent / bundleManager.install API
 * 在普通开发者证书下不可用，Phase 0 留空 stub，调用时直接通知失败。
 */
export class HapDownloader {
  private listeners: Array<(progress: number | null, error: string | null) => void> = []

  constructor(_context: common.UIAbilityContext) {}

  subscribe(listener: (progress: number | null, error: string | null) => void): void {
    this.listeners.push(listener)
  }

  unsubscribe(listener: (progress: number | null, error: string | null) => void): void {
    this.listeners = this.listeners.filter((l: (progress: number | null, error: string | null) => void) => l !== listener)
  }

  private notify(progress: number | null, error: string | null): void {
    for (const l of this.listeners) {
      try { l(progress, error) } catch (e) {}
    }
  }

  async startDownload(_url: string, _version: string): Promise<string> {
    this.notify(null, '应用升级在 HarmonyOS NEXT 端暂未实现，请用 hdc install 更新')
    return ''
  }
}
