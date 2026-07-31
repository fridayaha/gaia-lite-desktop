import { MineViewModel } from './MineViewModel';
import { MineUiState } from './MineUiState';
import { AppContainer } from '../../di/AppContainer';
import { TopBar, SettingsCard, SettingsRow, SettingsToggleRow } from './SettingsCommons';
import { AppRouter, Routes } from '../nav/Routes';
import { LightTheme } from '../theme/Colors';
import { IconPaths } from '../theme/Icons';

/**
 * 设置页。复刻 Android ui/mine/SettingsScreen.kt。
 * 账号设置 / [开发者模式 + 检查更新 + 版本 + 关于] / [切换账号 + 退出登录]
 */

@Component
export struct SettingsScreen {
  @Consume('container') container: AppContainer
  @State state: MineUiState = new MineUiState()
  private vm: MineViewModel | null = null
  private listener: (s: MineUiState) => void = (s) => { this.state = s }

  aboutToAppear(): void {
    this.vm = new MineViewModel(
      this.container.authRepository,
      this.container.agentContext,
      this.container.appReleaseRepository,
      this.container.messageFeedbackRepository,
      this.container.developerModeStore,
      this.container.accountStore,
      this.container.hapDownloader,
      this.container.serverConfig,
      this.container.tokenStorage,
    )
    this.vm.subscribe(this.listener)
    this.vm.bootstrap()
  }

  aboutToDisappear(): void {
    if (this.vm !== null) this.vm.unsubscribe(this.listener)
  }

  onBack(): void {
    AppRouter.instance().pop()
  }

  onAccountSettings(): void {
    AppRouter.instance().navigate(Routes.ACCOUNT_SETTINGS)
  }

  onSwitchAccount(): void {
    AppRouter.instance().navigate(Routes.SWITCH_ACCOUNT)
  }

  onCheckUpdate(): void {
    AppRouter.instance().navigate(Routes.UPDATE)
  }

  onVersionInfo(): void {
    AppRouter.instance().navigate(Routes.VERSION_INFO)
  }

  onAbout(): void {
    AppRouter.instance().navigate(Routes.ABOUT)
  }

  onLogout(): void {
    this.vm?.logout(() => {
      AppRouter.instance().replace(Routes.LOGIN)
    })
  }

  async onToggleDeveloperMode(on: boolean): Promise<void> {
    await this.vm?.setDeveloperMode(on)
  }

  @Builder
  accountCard(): void {
    SettingsRow('账号设置', () => this.onAccountSettings(), '', false, LightTheme.text, IconPaths.manageAccounts)
  }

  @Builder
  toolsCard(): void {
    SettingsToggleRow({
      title: '开发者模式',
      leadingIcon: IconPaths.code,
      checked: this.state.developerMode,
      showDivider: true,
      onToggle: (on: boolean) => { this.onToggleDeveloperMode(on) },
    })
    SettingsRow('检查更新', () => this.onCheckUpdate(), this.state.appVersion, true, LightTheme.text, IconPaths.systemUpdate)
    SettingsRow('版本', () => this.onVersionInfo(), this.state.appVersion, true, LightTheme.text, IconPaths.settings)
    SettingsRow('关于', () => this.onAbout(), '', false, LightTheme.text, IconPaths.info)
  }

  @Builder
  logoutCard(): void {
    SettingsRow('切换账号', () => this.onSwitchAccount(), '', true, LightTheme.text, IconPaths.manageAccounts)
    SettingsRow('退出登录', () => this.onLogout(), '', false, LightTheme.error, IconPaths.logout, LightTheme.error)
  }

  build(): void {
    Column() {
      TopBar('设置', () => this.onBack())
      Scroll() {
        Column() {
          SettingsCard({ content: (): void => this.accountCard() })

          // 中间留白（android 用 Spacer(weight=1) 占满剩余空间）
          Blank()
            .height(12)

          SettingsCard({ content: (): void => this.toolsCard() })

          Blank()
            .height(12)

          SettingsCard({ content: (): void => this.logoutCard() })
        }
        .width('100%')
        .padding({ left: 16, right: 16, top: 8, bottom: 16 })
        // Scroll 内容不足一屏时默认居中；撑满高度强制顶部对齐
        .constraintSize({ minHeight: '100%' })
      }
      .layoutWeight(1)
      .scrollBar(BarState.Off)
    }
    .width('100%')
    .height('100%')
    .backgroundColor(LightTheme.bg)
  }
}
