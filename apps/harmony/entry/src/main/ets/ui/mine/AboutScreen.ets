import { MineViewModel } from './MineViewModel';
import { MineUiState } from './MineUiState';
import { AppContainer } from '../../di/AppContainer';
import { TopBar, SettingsCard } from './SettingsCommons';
import { AppRouter } from '../nav/Routes';
import { LightTheme } from '../theme/Colors';
import { Typography } from '../theme/Typography';

/**
 * 关于页。复刻 Android ui/mine/AboutScreen.kt。
 * 只显示 App 名称 + 版本号（版本描述移到「版本」详情页）。
 */

@Component
export struct AboutScreen {
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

  @Builder
  aboutCard(): void {
    Column() {
      Text('知行')
        .fontSize(Typography.headlineSmall)
        .fontWeight(FontWeight.Medium)
        .fontColor(LightTheme.text)
      Text(`v${this.state.appVersion}`)
        .fontSize(Typography.bodyMedium)
        .fontColor(LightTheme.muted)
        .margin({ top: 8 })
    }
    .width('100%')
    .padding({ top: 32, bottom: 32 })
    .alignItems(HorizontalAlign.Center)
  }

  build(): void {
    Column() {
      TopBar('关于', () => this.onBack())
      Scroll() {
        Column() {
          SettingsCard({ content: (): void => this.aboutCard() })
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
