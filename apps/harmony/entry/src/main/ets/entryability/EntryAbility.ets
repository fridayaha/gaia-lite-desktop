import UIAbility from '@ohos.app.ability.UIAbility';
import AbilityConstant from '@ohos.app.ability.AbilityConstant';
import ConfigurationConstant from '@ohos.app.ability.ConfigurationConstant';
import Want from '@ohos.app.ability.Want';
import window from '@ohos.window';
import { AppContainer } from '../di/AppContainer';
import { logI } from '../util/Logger';

/**
 * Stage 模型入口 Ability。复刻 Android MainActivity（@AndroidEntryPoint + setContent）。
 * - onCreate：实例化 AppContainer（所有依赖注入）
 * - onWindowStageCreate：loadContent('pages/Index') 启动页
 * - 启动页 Index.ets 解析 token + lastAgent → 决定首屏路由
 */
export default class EntryAbility extends UIAbility {
  private container: AppContainer | null = null

  onCreate(want: Want, launchParam: AbilityConstant.LaunchParam): void {
    logI('EntryAbility', `onCreate ${want.action ?? ''}`)
    // app 只有浅色主题（对齐 android LightTheme），锁定 colorMode 防止系统深色模式
    // 渗透进 TextInput/弹窗等组件的默认样式（输入框黑底的根因）
    this.context.getApplicationContext().setColorMode(ConfigurationConstant.ColorMode.COLOR_MODE_LIGHT)
    this.container = new AppContainer(this.context)
    // 异步启动恢复（不阻塞 onCreate）
    this.container.onLaunch().catch((e: Error) => {
      logI('EntryAbility', `onLaunch failed: ${e instanceof Error ? e.message : String(e)}`)
    })
  }

  onWindowStageCreate(windowStage: window.WindowStage): void {
    logI('EntryAbility', 'onWindowStageCreate')
    // 把 container 注入 AppStorage，供 Index 和后续页面通过 AppStorage.get 取
    if (this.container !== null) {
      AppStorage.setOrCreate<AppContainer>('container', this.container)
    }
    windowStage.loadContent('pages/Index', (err) => {
      if (err) {
        logI('EntryAbility', `loadContent failed: ${err.message ?? JSON.stringify(err)}`)
      }
    })
  }

  onDestroy(): void {
    logI('EntryAbility', 'onDestroy')
    this.container = null
  }
}
